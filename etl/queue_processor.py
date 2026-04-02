"""
queue_processor.py — Procesador de cola Redis para incidentes en tiempo real.

Arquitectura:
  Productores (WebSockets, apps móviles, APIs) → enqueue() → Redis List
                                                               ↓ BLPOP
                                               QueueProcessor.start_consumer()
                                                               ↓
                                                   DBWriter.write_batch()
                                                               ↓
                                               PUBLISH sigmac:realtime:channel
                                               (consumido por WebSocket servers)

Garantías:
  - Procesamiento en micro-lotes de MICRO_BATCH_SIZE registros o cada
    FLUSH_INTERVAL_SECONDS segundos (lo que ocurra primero).
  - BLPOP bloqueante con timeout para evitar busy-wait.
  - Logging estructurado en cada etapa.
  - Shutdown limpio con señal externa (flag _running).
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

import redis
import structlog
from pydantic import ValidationError

from etl.config import settings
from etl.db_writer import DBWriter
from etl.models import IncidenteNormalizado, IncidenteRaw

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
QUEUE_KEY = "sigmac:incidentes:queue"
REALTIME_CHANNEL = "sigmac:realtime:channel"
MICRO_BATCH_SIZE = 50
FLUSH_INTERVAL_SECONDS = 2.0
BLPOP_TIMEOUT_SECONDS = 1  # timeout del BLPOP; permite comprobar el flag de shutdown


class QueueProcessor:
    """
    Consumidor de la cola Redis `sigmac:incidentes:queue`.

    Ciclo de vida
    -------------
    processor = QueueProcessor()
    processor.start_consumer()   # bloqueante; ejecutar en hilo separado

    Para shutdown:
        processor.stop()
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        db_writer: Optional[DBWriter] = None,
    ) -> None:
        self._redis_url = redis_url or settings.REDIS_URL
        self._redis: redis.Redis = redis.Redis.from_url(
            self._redis_url,
            decode_responses=True,
        )
        self._writer = db_writer or DBWriter()
        self._running = False
        self._lock = threading.Lock()
        self._log = log.bind(component="QueueProcessor")

    # ------------------------------------------------------------------
    # API de producción (enqueue)
    # ------------------------------------------------------------------

    def enqueue(self, incidente_dict: Dict[str, Any]) -> None:
        """
        Publica un incidente en la cola Redis.

        El valor se serializa a JSON y se añade al final de la lista
        (RPUSH) para procesamiento FIFO.

        Parámetros
        ----------
        incidente_dict : dict
            Diccionario compatible con el modelo IncidenteRaw.
        """
        payload = json.dumps(incidente_dict, default=str, ensure_ascii=False)
        length = self._redis.rpush(QUEUE_KEY, payload)
        self._log.debug(
            "enqueued",
            queue=QUEUE_KEY,
            queue_length=length,
        )

    # ------------------------------------------------------------------
    # API de consumo (start_consumer / stop)
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Solicita el shutdown del bucle de consumo."""
        self._log.info("consumer_stop_requested")
        self._running = False

    def start_consumer(self) -> None:
        """
        Inicia el bucle de consumo bloqueante.

        Estrategia de micro-lotes:
        - Acumula mensajes de BLPOP hasta MICRO_BATCH_SIZE o hasta que
          transcurran FLUSH_INTERVAL_SECONDS segundos sin completar el lote.
        - Procesa el lote llamando a _process_batch().
        - Publica los registros normalizados en REALTIME_CHANNEL.
        """
        self._running = True
        self._log.info(
            "consumer_started",
            queue=QUEUE_KEY,
            micro_batch_size=MICRO_BATCH_SIZE,
            flush_interval_s=FLUSH_INTERVAL_SECONDS,
        )

        buffer: List[str] = []
        last_flush = time.monotonic()

        while self._running:
            # BLPOP bloqueante con timeout corto para permitir flush por tiempo
            result = self._redis.blpop(
                QUEUE_KEY,
                timeout=BLPOP_TIMEOUT_SECONDS,
            )

            if result is not None:
                _key, payload = result
                buffer.append(payload)

            elapsed = time.monotonic() - last_flush
            should_flush = (
                len(buffer) >= MICRO_BATCH_SIZE
                or (buffer and elapsed >= FLUSH_INTERVAL_SECONDS)
            )

            if should_flush:
                self._flush_buffer(buffer)
                buffer = []
                last_flush = time.monotonic()

        # Flush final al apagarse
        if buffer:
            self._log.info("consumer_final_flush", pending=len(buffer))
            self._flush_buffer(buffer)

        self._log.info("consumer_stopped")

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _flush_buffer(self, buffer: List[str]) -> None:
        """
        Procesa un micro-lote de mensajes JSON crudos:
        1. Deserializa y valida con IncidenteRaw.
        2. Normaliza a IncidenteNormalizado.
        3. Escribe en BD con DBWriter.write_batch().
        4. Publica en canal Redis para WebSockets.
        """
        if not buffer:
            return

        start = time.monotonic()
        raw_list: List[IncidenteRaw] = []
        errores_parse = 0

        for payload in buffer:
            try:
                data = json.loads(payload)
                raw = IncidenteRaw(**data)
                raw_list.append(raw)
            except (json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
                errores_parse += 1
                self._log.warning(
                    "queue_parse_error",
                    error=str(exc),
                    payload_preview=payload[:120],
                )

        normalizados: List[IncidenteNormalizado] = [
            IncidenteNormalizado.from_raw(r) for r in raw_list
        ]

        written = 0
        if normalizados:
            try:
                written = self._writer.write_batch(normalizados)
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "queue_write_error",
                    error=str(exc),
                    batch_size=len(normalizados),
                )

        # Publicar en canal de tiempo real para que los WebSocket servers
        # distribuyan las actualizaciones a los clientes conectados.
        if normalizados:
            self._publish_realtime(normalizados)

        elapsed = round(time.monotonic() - start, 3)
        self._log.info(
            "micro_batch_processed",
            recibidos=len(buffer),
            parseados=len(raw_list),
            normalizados=len(normalizados),
            escritos=written,
            errores_parse=errores_parse,
            elapsed_s=elapsed,
        )

    def _publish_realtime(self, incidentes: List[IncidenteNormalizado]) -> None:
        """
        Publica los incidentes normalizados en el canal Redis pub/sub
        `sigmac:realtime:channel` para su consumo por WebSocket servers.

        Formato del mensaje:
        {
            "tipo": "nuevos_incidentes",
            "cantidad": <int>,
            "incidentes": [ { ...campos... }, ... ]
        }
        """
        payload = json.dumps(
            {
                "tipo": "nuevos_incidentes",
                "cantidad": len(incidentes),
                "incidentes": [
                    {
                        "id": str(inc.id),
                        "latitud": inc.latitud,
                        "longitud": inc.longitud,
                        "tipo_incidente": inc.tipo_incidente,
                        "severidad": inc.severidad,
                        "timestamp": inc.timestamp.isoformat(),
                        "sistema_origen": inc.sistema_origen,
                    }
                    for inc in incidentes
                ],
            },
            ensure_ascii=False,
        )
        try:
            subscribers = self._redis.publish(REALTIME_CHANNEL, payload)
            self._log.debug(
                "realtime_published",
                channel=REALTIME_CHANNEL,
                incidentes=len(incidentes),
                subscribers=subscribers,
            )
        except redis.RedisError as exc:
            self._log.error(
                "realtime_publish_error",
                channel=REALTIME_CHANNEL,
                error=str(exc),
            )
