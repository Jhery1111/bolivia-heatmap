"""
websocket_handler.py
--------------------
Real-time incident broadcasting via WebSocket + Redis PubSub.

Flow
----
1. Client connects to ``ws://host/ws/live-incidents?token=<JWT>``.
2. The JWT is validated; invalid tokens are rejected immediately.
3. A background asyncio Task subscribes to the Redis PubSub channel
   ``sigmac:realtime:channel`` and forwards every message to ALL
   currently connected WebSocket clients.
4. A heartbeat ping is sent every 30 s to keep proxies from timing out.

Message format sent to clients
-------------------------------
.. code-block:: json

    {
        "type": "NEW_INCIDENT",
        "data": {
            "id": 42,
            "lat": -16.5,
            "lon": -68.1,
            "tipo": "ACCIDENTE",
            "severidad": 3,
            "timestamp": "2024-06-01T12:00:00+00:00",
            "sistema_origen": "ETL_TRANSITO"
        }
    }
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Dict

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from .auth import verify_token
from .config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])

_HEARTBEAT_INTERVAL = 30  # seconds
_REDIS_CHANNEL = "sigmac:realtime:channel"


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Tracks active WebSocket connections and provides broadcast utilities."""

    def __init__(self) -> None:
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept the WebSocket and register it under *client_id*."""
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info("WS client connected", client_id=client_id, total=len(self.active_connections))

    def disconnect(self, client_id: str) -> None:
        """Remove *client_id* from the active-connections registry."""
        self.active_connections.pop(client_id, None)
        logger.info("WS client disconnected", client_id=client_id, total=len(self.active_connections))

    async def broadcast(self, message: str) -> None:
        """Send *message* to every connected client; silently drop dead sockets."""
        dead: list[str] = []
        for client_id, ws in list(self.active_connections.items()):
            try:
                await ws.send_text(message)
            except Exception as exc:
                logger.warning("Broadcast failed for client", client_id=client_id, error=str(exc))
                dead.append(client_id)
        for client_id in dead:
            self.disconnect(client_id)

    async def send_personal(self, client_id: str, message: str) -> None:
        """Send *message* to a single client identified by *client_id*."""
        ws = self.active_connections.get(client_id)
        if ws is None:
            return
        try:
            await ws.send_text(message)
        except Exception as exc:
            logger.warning("Personal send failed", client_id=client_id, error=str(exc))
            self.disconnect(client_id)


# Module-level singleton shared across all WebSocket connections.
manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Redis PubSub listener (singleton background task)
# ---------------------------------------------------------------------------

_redis_listener_task: asyncio.Task | None = None


async def _redis_listener() -> None:
    """Subscribe to Redis PubSub and forward messages to all WS clients.

    Runs as a long-lived asyncio Task.  Reconnects automatically on errors.
    """
    import redis.asyncio as aioredis

    while True:
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe(_REDIS_CHANNEL)
            logger.info("Redis PubSub subscribed", channel=_REDIS_CHANNEL)

            async for raw_message in pubsub.listen():
                if raw_message["type"] != "message":
                    continue
                data: str = raw_message["data"]
                # Validate JSON before forwarding
                try:
                    json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Invalid JSON from Redis, skipping", raw=data[:200])
                    continue

                if manager.active_connections:
                    await manager.broadcast(data)

        except asyncio.CancelledError:
            logger.info("Redis listener task cancelled — shutting down")
            break
        except Exception as exc:
            logger.error("Redis listener error — reconnecting in 5 s", error=str(exc))
            await asyncio.sleep(5)
        finally:
            try:
                await pubsub.unsubscribe(_REDIS_CHANNEL)
                await r.aclose()
            except Exception:
                pass


async def start_redis_listener() -> None:
    """Launch the Redis PubSub listener task (idempotent)."""
    global _redis_listener_task
    if _redis_listener_task is None or _redis_listener_task.done():
        _redis_listener_task = asyncio.create_task(
            _redis_listener(), name="redis-pubsub-listener"
        )
        logger.info("Redis PubSub listener task started")


async def stop_redis_listener() -> None:
    """Cancel the Redis PubSub listener task gracefully."""
    global _redis_listener_task
    if _redis_listener_task and not _redis_listener_task.done():
        _redis_listener_task.cancel()
        try:
            await _redis_listener_task
        except asyncio.CancelledError:
            pass
    _redis_listener_task = None
    logger.info("Redis PubSub listener task stopped")


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/live-incidents")
async def websocket_live_incidents(
    websocket: WebSocket,
    token: str | None = None,
) -> None:
    """WebSocket endpoint for real-time incident streaming.

    Query param ``?token=<JWT>`` is required for authentication.
    """
    # ---- Authentication ----
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.warning("WS connection rejected: no token provided")
        return

    try:
        token_data = verify_token(token)
    except Exception as exc:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        logger.warning("WS connection rejected: invalid token", error=str(exc))
        return

    client_id = f"{token_data.username}_{id(websocket)}"

    # ---- Accept & register ----
    await manager.connect(websocket, client_id)

    # Send welcome message
    welcome = json.dumps(
        {
            "type": "CONNECTED",
            "data": {
                "client_id": client_id,
                "channel": _REDIS_CHANNEL,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    )
    await manager.send_personal(client_id, welcome)

    # ---- Heartbeat + receive loop ----
    try:
        while True:
            try:
                # Wait for either a client message or a heartbeat timeout.
                # receive_text() with timeout doubles as a liveness check.
                await asyncio.wait_for(websocket.receive_text(), timeout=_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                # Send heartbeat ping.
                ping = json.dumps(
                    {
                        "type": "PING",
                        "data": {"timestamp": datetime.now(timezone.utc).isoformat()},
                    }
                )
                await manager.send_personal(client_id, ping)
            # Client messages are silently ignored — this is a read-only stream.

    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as exc:
        logger.error("WS error", client_id=client_id, error=str(exc))
        manager.disconnect(client_id)
