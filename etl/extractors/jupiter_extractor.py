"""
extractors/jupiter_extractor.py — Extractor simulado para el sistema JÚPITER.

JÚPITER es una base de datos relacional PostgreSQL heredada con la tabla:
    incidentes_jupiter(id, x_coord, y_coord, tipo, nivel_alerta, fecha_hora, datos_extra)

En producción, este extractor se conectaría a la BD de JÚPITER mediante
settings.JUPITER_API_URL / settings.JUPITER_API_KEY.  En esta implementación,
la extracción se simula con datos aleatorios realistas para Bolivia.

Mapeo nivel_alerta → severidad:
    nivel_alerta (1-10)  →  severidad (1-5)
    ceil(nivel_alerta / 2)
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import structlog
from faker import Faker

from etl.models import IncidenteRaw

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Faker configurado para España (nombres en español coherentes con Bolivia)
# ---------------------------------------------------------------------------
_fake = Faker("es_ES")
Faker.seed(0)

# ---------------------------------------------------------------------------
# Geografía de Bolivia — bounding boxes de departamentos
# ---------------------------------------------------------------------------
# Formato: (lat_min, lat_max, lon_min, lon_max, peso_relativo)
_DEPARTAMENTOS: List[tuple[float, float, float, float, float]] = [
    # La Paz (área urbana densa)
    (-16.50, -16.00, -68.30, -68.00, 0.30),
    # Santa Cruz
    (-18.10, -17.50, -63.40, -63.00, 0.25),
    # Cochabamba
    (-17.55, -17.20, -66.30, -65.90, 0.15),
    # Oruro
    (-18.10, -17.80, -67.30, -66.90, 0.07),
    # Potosí
    (-19.70, -19.40, -65.90, -65.50, 0.07),
    # Sucre / Chuquisaca
    (-19.20, -18.90, -65.50, -65.00, 0.06),
    # Tarija
    (-21.60, -21.40, -64.90, -64.50, 0.04),
    # Beni
    (-15.00, -14.50, -65.20, -64.50, 0.03),
    # Pando
    (-11.20, -10.80, -69.00, -68.50, 0.03),
]

_PESOS = [d[4] for d in _DEPARTAMENTOS]

# ---------------------------------------------------------------------------
# Catálogo de tipos de incidente de JÚPITER
# ---------------------------------------------------------------------------
_TIPOS_JUPITER = [
    "ACCIDENTE_TRANSITO",
    "COLISION_MULTIPLE",
    "ATROPELLO",
    "VOLCAMIENTO",
    "FUGA_CONDUCTOR",
    "DAÑO_INFRAESTRUCTURA",
    "EMERGENCIA_MEDICA_VIA",
    "OBSTRUCCION_VIA",
    "INCENDIO_VEHICULAR",
]


def _random_coord_bolivia() -> tuple[float, float]:
    """Devuelve (latitud, longitud) aleatorias dentro del territorio boliviano."""
    depto = random.choices(_DEPARTAMENTOS, weights=_PESOS, k=1)[0]
    lat = random.uniform(depto[0], depto[1])
    lon = random.uniform(depto[2], depto[3])
    return round(lat, 6), round(lon, 6)


def _nivel_alerta_to_severidad(nivel: int) -> int:
    """
    Convierte nivel_alerta (1-10) de JÚPITER a severidad (1-5).
    Fórmula: ceil(nivel / 2), con clamp [1, 5].
    """
    return max(1, min(5, math.ceil(nivel / 2)))


def _simulate_jupiter_row(since: datetime) -> Dict[str, Any]:
    """
    Genera una fila simulada de `incidentes_jupiter` tal como llegaría
    de la BD relacional de JÚPITER.
    """
    lat, lon = _random_coord_bolivia()
    nivel_alerta = random.randint(1, 10)
    fecha_hora = _fake.date_time_between(
        start_date=since,
        end_date="now",
        tzinfo=timezone.utc,
    )
    return {
        "id": str(uuid.uuid4()),
        "x_coord": lon,   # JÚPITER usa convención (x=lon, y=lat)
        "y_coord": lat,
        "tipo": random.choice(_TIPOS_JUPITER),
        "nivel_alerta": nivel_alerta,
        "fecha_hora": fecha_hora,
        "datos_extra": {
            "placa": _fake.license_plate(),
            "via": _fake.street_name(),
            "observaciones": _fake.sentence(nb_words=8),
            "operador_id": _fake.numerify("OP-####"),
        },
    }


class JupiterExtractor:
    """
    Extractor de incidentes desde el sistema JÚPITER.

    En producción conecta a la BD PostgreSQL de JÚPITER mediante el DSN
    configurado en settings.JUPITER_API_URL / JUPITER_API_KEY.
    La implementación actual simula la extracción generando datos aleatorios.

    Ejemplo de uso
    --------------
    extractor = JupiterExtractor()
    incidentes: List[IncidenteRaw] = extractor.extract(since=datetime(...))
    """

    SISTEMA = "JUPITER"

    def __init__(self) -> None:
        self._log = log.bind(extractor="JupiterExtractor")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def extract(self, since: datetime) -> List[IncidenteRaw]:
        """
        Extrae incidentes de JÚPITER creados a partir de `since`.

        Parámetros
        ----------
        since : datetime
            Marca temporal (UTC) a partir de la cual buscar registros nuevos.

        Retorna
        -------
        List[IncidenteRaw]
            Incidentes validados y listos para normalización.
        """
        self._log.info("jupiter_extract_start", since=since.isoformat())

        raw_rows = self._fetch_rows(since)
        incidentes: List[IncidenteRaw] = []
        errores = 0

        for row in raw_rows:
            try:
                incidente = self._map_row(row)
                incidentes.append(incidente)
            except Exception as exc:  # noqa: BLE001
                errores += 1
                self._log.warning(
                    "jupiter_row_mapping_error",
                    row_id=row.get("id"),
                    error=str(exc),
                )

        self._log.info(
            "jupiter_extract_complete",
            total_rows=len(raw_rows),
            mapped=len(incidentes),
            errores=errores,
        )
        return incidentes

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _fetch_rows(self, since: datetime) -> List[Dict[str, Any]]:
        """
        Simula la consulta SQL:
            SELECT id, x_coord, y_coord, tipo, nivel_alerta, fecha_hora, datos_extra
            FROM incidentes_jupiter
            WHERE fecha_hora >= %(since)s
            ORDER BY fecha_hora ASC;

        En producción, conectaría a la BD de JÚPITER con psycopg2.
        """
        n = random.randint(10, 100)
        self._log.debug("jupiter_simulating_rows", n=n)
        return [_simulate_jupiter_row(since) for _ in range(n)]

    def _map_row(self, row: Dict[str, Any]) -> IncidenteRaw:
        """
        Transforma una fila cruda de JÚPITER al modelo IncidenteRaw.

        Conversiones:
        - x_coord → longitud, y_coord → latitud
        - nivel_alerta (1-10) → severidad (1-5)
        - tipo → tipo_incidente
        - datos_extra → metadata
        """
        return IncidenteRaw(
            id_sistema_origen=str(row["id"]),
            sistema_origen=self.SISTEMA,
            tipo_incidente=str(row["tipo"]),
            severidad=_nivel_alerta_to_severidad(int(row["nivel_alerta"])),
            timestamp=row["fecha_hora"],
            latitud=float(row["y_coord"]),
            longitud=float(row["x_coord"]),
            metadata={
                "nivel_alerta_original": row["nivel_alerta"],
                **row.get("datos_extra", {}),
            },
        )
