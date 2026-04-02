"""
sync_job.py — Job de sincronización principal del ETL SIGMAC.

Responsabilidades:
  1. Ejecutar periódicamente un ciclo de extracción de JÚPITER y MINERVA.
  2. Normalizar los incidentes crudos al modelo interno.
  3. Persistir en PostgreSQL/PostGIS mediante DBWriter.bulk_upsert().
  4. Registrar métricas de cada ciclo con structlog.
  5. Responder a SIGTERM/SIGINT para un shutdown limpio.

Uso:
    python -m etl.sync_job
    # o directamente:
    python etl/sync_job.py
"""

from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import schedule
import structlog

from etl.config import settings
from etl.db_writer import DBWriter
from etl.extractors.jupiter_extractor import JupiterExtractor
from etl.extractors.minerva_extractor import MinervaExtractor
from etl.models import IncidenteNormalizado, IncidenteRaw

# ---------------------------------------------------------------------------
# Configuración de logging estructurado
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.dev.ConsoleRenderer()
        if settings.LOG_LEVEL == "DEBUG"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), settings.LOG_LEVEL, 20)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger("sync_job")

# ---------------------------------------------------------------------------
# Estado global del proceso
# ---------------------------------------------------------------------------
_shutdown_requested = False
_writer: Optional[DBWriter] = None


def _handle_signal(signum: int, _frame: object) -> None:
    """Handler para SIGTERM y SIGINT — solicita shutdown limpio."""
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    log.warning("shutdown_signal_received", signal=sig_name)
    _shutdown_requested = True
    # Cancelar todos los jobs de schedule para que el loop finalice
    schedule.clear()


# ---------------------------------------------------------------------------
# Ciclo de sincronización
# ---------------------------------------------------------------------------

def run_sync_cycle(
    since: Optional[datetime] = None,
    writer: Optional[DBWriter] = None,
) -> dict:
    """
    Ejecuta un ciclo completo de sincronización ETL:
    1. Extrae incidentes de JÚPITER (simulado).
    2. Extrae incidentes de MINERVA (simulado).
    3. Normaliza todos los registros crudos.
    4. Persiste en BD mediante bulk_upsert.

    Parámetros
    ----------
    since : datetime | None
        Marca temporal de inicio de la ventana de extracción.
        Por defecto: ahora - SYNC_INTERVAL_SECONDS.
    writer : DBWriter | None
        Instancia de DBWriter a usar.  Si es None, usa la global.

    Retorna
    -------
    dict con métricas del ciclo.
    """
    global _writer

    cycle_start = time.monotonic()
    cycle_ts = datetime.now(tz=timezone.utc)

    if since is None:
        since = cycle_ts - timedelta(seconds=settings.SYNC_INTERVAL_SECONDS)

    effective_writer = writer or _writer
    if effective_writer is None:
        effective_writer = DBWriter()
        _writer = effective_writer

    log.info(
        "sync_cycle_start",
        cycle_ts=cycle_ts.isoformat(),
        since=since.isoformat(),
        interval_s=settings.SYNC_INTERVAL_SECONDS,
    )

    # ------------------------------------------------------------------
    # 1. Extracción
    # ------------------------------------------------------------------
    jupiter_raw: List[IncidenteRaw] = []
    minerva_raw: List[IncidenteRaw] = []
    extraction_errors = 0

    try:
        jupiter_extractor = JupiterExtractor()
        jupiter_raw = jupiter_extractor.extract(since=since)
        log.info("extracted_jupiter", registros=len(jupiter_raw))
    except Exception as exc:  # noqa: BLE001
        extraction_errors += 1
        log.error("jupiter_extraction_failed", error=str(exc))

    try:
        minerva_extractor = MinervaExtractor()
        minerva_raw = minerva_extractor.extract(since=since)
        log.info("extracted_minerva", registros=len(minerva_raw))
    except Exception as exc:  # noqa: BLE001
        extraction_errors += 1
        log.error("minerva_extraction_failed", error=str(exc))

    all_raw = jupiter_raw + minerva_raw
    total_extraidos = len(all_raw)

    # ------------------------------------------------------------------
    # 2. Normalización
    # ------------------------------------------------------------------
    normalizados: List[IncidenteNormalizado] = []
    errores_normalizacion = 0

    for raw in all_raw:
        try:
            normalizados.append(IncidenteNormalizado.from_raw(raw))
        except Exception as exc:  # noqa: BLE001
            errores_normalizacion += 1
            log.warning(
                "normalizacion_error",
                id_sistema_origen=raw.id_sistema_origen,
                sistema=raw.sistema_origen,
                error=str(exc),
            )

    log.info(
        "normalizacion_complete",
        extraidos=total_extraidos,
        normalizados=len(normalizados),
        errores=errores_normalizacion,
    )

    # ------------------------------------------------------------------
    # 3. Escritura en BD
    # ------------------------------------------------------------------
    escritos = 0
    errores_escritura = 0

    if normalizados:
        try:
            escritos = effective_writer.bulk_upsert(normalizados)
        except Exception as exc:  # noqa: BLE001
            errores_escritura += 1
            log.error(
                "escritura_error",
                error=str(exc),
                intentados=len(normalizados),
            )

    # ------------------------------------------------------------------
    # 4. Métricas del ciclo
    # ------------------------------------------------------------------
    elapsed = round(time.monotonic() - cycle_start, 3)

    metrics = {
        "cycle_ts": cycle_ts.isoformat(),
        "since": since.isoformat(),
        "extraidos_jupiter": len(jupiter_raw),
        "extraidos_minerva": len(minerva_raw),
        "total_extraidos": total_extraidos,
        "normalizados": len(normalizados),
        "errores_normalizacion": errores_normalizacion,
        "escritos": escritos,
        "errores_escritura": errores_escritura,
        "errores_extraccion": extraction_errors,
        "elapsed_s": elapsed,
    }

    log.info("sync_cycle_complete", **metrics)
    return metrics


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Inicia el job de sincronización con schedule.

    Registra SIGTERM y SIGINT para shutdown graceful y ejecuta
    run_sync_cycle() cada settings.SYNC_INTERVAL_SECONDS segundos.
    """
    global _writer, _shutdown_requested

    log.info(
        "sync_job_starting",
        interval_s=settings.SYNC_INTERVAL_SECONDS,
        batch_size=settings.BATCH_SIZE,
        log_level=settings.LOG_LEVEL,
    )

    # Registrar handlers de señal
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Inicializar writer global (una sola vez para reutilizar el pool)
    try:
        _writer = DBWriter()
    except Exception as exc:
        log.error("db_writer_init_failed", error=str(exc))
        sys.exit(1)

    # Programar el ciclo de sincronización
    interval = settings.SYNC_INTERVAL_SECONDS
    schedule.every(interval).seconds.do(run_sync_cycle)

    log.info(
        "sync_job_scheduled",
        interval_s=interval,
        next_run=str(schedule.next_run()),
    )

    # Ejecutar el primer ciclo inmediatamente al arrancar
    run_sync_cycle()

    # Bucle principal
    try:
        while not _shutdown_requested:
            schedule.run_pending()
            # Dormir en intervalos cortos para reaccionar rápido a señales
            time.sleep(1)
    finally:
        log.info("sync_job_shutting_down")
        if _writer:
            _writer.close()
        log.info("sync_job_stopped")


if __name__ == "__main__":
    main()
