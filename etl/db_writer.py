"""
db_writer.py — Escritor en lotes a PostgreSQL/PostGIS para SIGMAC.

Estrategia:
  - Connection pool con SimpleConnectionPool (min=2, max=10).
  - bulk_upsert usa execute_values para inserción masiva eficiente.
  - ON CONFLICT (id_sistema_origen, sistema_origen) DO NOTHING evita duplicados.
  - Reintentos con tenacity en caso de errores transitorios de BD.
  - Logging estructurado con structlog.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

import psycopg2
import psycopg2.pool
import structlog
from psycopg2.extras import execute_values
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from etl.config import settings
from etl.models import IncidenteNormalizado

log = structlog.get_logger(__name__)

# SQL de inserción masiva.
# La columna `geom` es generada: ST_SetSRID(ST_MakePoint(longitud, latitud), 4326)
# pero la insertamos explícitamente via ST_GeomFromText para mayor claridad.
_INSERT_SQL = """
INSERT INTO sigmac.core_incidentes (
    id,
    latitud,
    longitud,
    geom,
    tipo_incidente,
    severidad,
    timestamp,
    id_sistema_origen,
    sistema_origen,
    metadata
)
VALUES %s
ON CONFLICT (id_sistema_origen, sistema_origen) DO NOTHING
"""

_ROW_TEMPLATE = """(
    %s::uuid,
    %s,
    %s,
    ST_GeomFromText(%s, 4326),
    %s,
    %s,
    %s,
    %s,
    %s,
    %s::jsonb
)"""


def _build_row(inc: IncidenteNormalizado) -> tuple:
    """Convierte un IncidenteNormalizado en una tupla para execute_values."""
    return (
        str(inc.id),
        inc.latitud,
        inc.longitud,
        inc.geom_wkt,
        inc.tipo_incidente,
        inc.severidad,
        inc.timestamp,
        inc.id_sistema_origen,
        inc.sistema_origen,
        json.dumps(inc.metadata, ensure_ascii=False, default=str),
    )


class DBWriter:
    """
    Gestor de escritura en lotes hacia la tabla sigmac.core_incidentes.

    Uso típico
    ----------
    writer = DBWriter()
    writer.bulk_upsert(lista_de_incidentes_normalizados)
    writer.close()
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        min_conn: int = 2,
        max_conn: int = 10,
    ) -> None:
        self._dsn = dsn or settings.DATABASE_URL
        self._pool: Optional[psycopg2.pool.SimpleConnectionPool] = None
        self._min_conn = min_conn
        self._max_conn = max_conn
        self._init_pool()

    # ---------------------------------------------------------------- #
    # Pool                                                               #
    # ---------------------------------------------------------------- #

    def _init_pool(self) -> None:
        log.info("db_pool_init", dsn=self._dsn, min=self._min_conn, max=self._max_conn)
        self._pool = psycopg2.pool.SimpleConnectionPool(
            self._min_conn,
            self._max_conn,
            self._dsn,
        )

    def _get_conn(self) -> psycopg2.extensions.connection:
        assert self._pool is not None, "Pool no inicializado."
        return self._pool.getconn()

    def _put_conn(self, conn: psycopg2.extensions.connection) -> None:
        assert self._pool is not None
        self._pool.putconn(conn)

    def close(self) -> None:
        """Cierra todas las conexiones del pool."""
        if self._pool:
            self._pool.closeall()
            log.info("db_pool_closed")

    # ---------------------------------------------------------------- #
    # Escritura pública                                                  #
    # ---------------------------------------------------------------- #

    def write_batch(self, incidentes: List[IncidenteNormalizado]) -> int:
        """
        Wrapper público para bulk_upsert.
        Devuelve el número de filas afectadas.
        """
        return self.bulk_upsert(incidentes)

    @retry(
        retry=retry_if_exception_type(
            (psycopg2.OperationalError, psycopg2.InterfaceError)
        ),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def bulk_upsert(self, incidentes: List[IncidenteNormalizado]) -> int:
        """
        Inserta una lista de IncidenteNormalizado en lotes usando execute_values.
        Ignora duplicados (ON CONFLICT DO NOTHING).

        Parámetros
        ----------
        incidentes : lista de IncidenteNormalizado

        Retorna
        -------
        int — número de filas efectivamente insertadas.
        """
        if not incidentes:
            log.debug("bulk_upsert_empty_batch")
            return 0

        batch_size = settings.BATCH_SIZE
        total_written = 0
        start = time.monotonic()

        conn = self._get_conn()
        try:
            with conn:  # transacción automática
                with conn.cursor() as cur:
                    # Procesar en sub-lotes del tamaño configurado
                    for i in range(0, len(incidentes), batch_size):
                        sub_batch = incidentes[i : i + batch_size]
                        rows = [_build_row(inc) for inc in sub_batch]

                        execute_values(
                            cur,
                            _INSERT_SQL,
                            rows,
                            template=_ROW_TEMPLATE,
                            page_size=batch_size,
                        )
                        written = cur.rowcount if cur.rowcount >= 0 else len(sub_batch)
                        total_written += written

                        log.debug(
                            "sub_batch_written",
                            sub_batch_size=len(sub_batch),
                            rowcount=cur.rowcount,
                        )

            elapsed = time.monotonic() - start
            log.info(
                "bulk_upsert_complete",
                total_input=len(incidentes),
                total_written=total_written,
                elapsed_s=round(elapsed, 3),
                rate_per_s=round(len(incidentes) / max(elapsed, 0.001)),
            )
        except psycopg2.Error as exc:
            log.error(
                "bulk_upsert_error",
                error=str(exc),
                pgcode=getattr(exc, "pgcode", None),
                batch_size=len(incidentes),
            )
            # Reintentar solo en errores operacionales (red, BD caída, etc.)
            if isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
                # Descartar conexión rota antes del reintento
                try:
                    self._pool.putconn(conn, close=True)  # type: ignore[union-attr]
                except Exception:
                    pass
                conn = None  # type: ignore[assignment]
                raise
            raise
        finally:
            if conn is not None:
                self._put_conn(conn)

        return total_written
