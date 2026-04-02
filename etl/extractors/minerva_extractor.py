"""
extractors/minerva_extractor.py — Extractor simulado para el sistema MINERVA.

MINERVA expone una API REST que devuelve páginas JSON con la estructura:
    {
        "total": <int>,
        "pagina": <int>,
        "por_pagina": <int>,
        "eventos": [
            {
                "evento_id": "<str>",
                "coordenadas": { "latitud": <float>, "longitud": <float> },
                "categoria": "<str>",
                "prioridad": "BAJA" | "MEDIA" | "ALTA" | "CRITICA",
                "timestamp_utc": "<ISO 8601>"
            },
            ...
        ]
    }

En producción, las llamadas se harían con httpx contra settings.MINERVA_DB_URL.
La implementación actual simula las respuestas paginadas con datos aleatorios.

Mapeo prioridad → severidad:
    BAJA    → 1
    MEDIA   → 2-3  (aleatorio para distribución realista)
    ALTA    → 4
    CRITICA → 5
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import structlog
from faker import Faker

from etl.models import IncidenteRaw

log = structlog.get_logger(__name__)

_fake = Faker("es_ES")
Faker.seed(42)

# ---------------------------------------------------------------------------
# Geografía: mismos bounding boxes que JupiterExtractor (centralizado aquí
# para no crear dependencias circulares; en una base de código más grande
# esto iría a etl/geo_utils.py).
# ---------------------------------------------------------------------------
_DEPARTAMENTOS: List[tuple[float, float, float, float, float]] = [
    (-16.50, -16.00, -68.30, -68.00, 0.28),
    (-18.10, -17.50, -63.40, -63.00, 0.27),
    (-17.55, -17.20, -66.30, -65.90, 0.14),
    (-18.10, -17.80, -67.30, -66.90, 0.07),
    (-19.70, -19.40, -65.90, -65.50, 0.07),
    (-19.20, -18.90, -65.50, -65.00, 0.06),
    (-21.60, -21.40, -64.90, -64.50, 0.05),
    (-15.00, -14.50, -65.20, -64.50, 0.03),
    (-11.20, -10.80, -69.00, -68.50, 0.03),
]
_PESOS = [d[4] for d in _DEPARTAMENTOS]

# ---------------------------------------------------------------------------
# Catálogo de categorías de evento de MINERVA
# ---------------------------------------------------------------------------
_CATEGORIAS_MINERVA = [
    "INFRACCION_TRANSITO",
    "CONDUCCION_IMPRUDENTE",
    "EXCESO_VELOCIDAD",
    "CONDUCCION_EBRIEDAD",
    "VEHICULO_ABANDONADO",
    "BLOQUEO_VIA",
    "ROBO_VEHICULO",
    "FUGA_GASES",
    "DERRAME_COMBUSTIBLE",
    "LESIONES_TRANSITO",
]

# ---------------------------------------------------------------------------
# Mapeo prioridad textual → severidad numérica
# ---------------------------------------------------------------------------
_PRIORIDAD_SEVERIDAD: Dict[str, int] = {
    "BAJA": 1,
    "MEDIA": 3,
    "ALTA": 4,
    "CRITICA": 5,
}

# MEDIA tiene rango 2-3; para modelar variabilidad se aplica ruido al mapear.
_PRIORIDAD_RANGO: Dict[str, tuple[int, int]] = {
    "BAJA": (1, 1),
    "MEDIA": (2, 3),
    "ALTA": (4, 4),
    "CRITICA": (5, 5),
}

_PAGE_SIZE = 25  # registros por página simulada


def _random_coord_bolivia() -> tuple[float, float]:
    depto = random.choices(_DEPARTAMENTOS, weights=_PESOS, k=1)[0]
    lat = random.uniform(depto[0], depto[1])
    lon = random.uniform(depto[2], depto[3])
    return round(lat, 6), round(lon, 6)


def _prioridad_to_severidad(prioridad: str) -> int:
    rango = _PRIORIDAD_RANGO.get(prioridad.upper())
    if rango is None:
        log.warning("minerva_prioridad_desconocida", prioridad=prioridad)
        return 3  # valor neutro por defecto
    return random.randint(rango[0], rango[1])


def _simulate_minerva_page(
    since: datetime,
    pagina: int,
    total: int,
) -> Dict[str, Any]:
    """
    Genera una página simulada de la API de MINERVA.
    """
    inicio = (pagina - 1) * _PAGE_SIZE
    fin = min(inicio + _PAGE_SIZE, total)
    eventos = []
    for _ in range(fin - inicio):
        lat, lon = _random_coord_bolivia()
        prioridad = random.choices(
            ["BAJA", "MEDIA", "ALTA", "CRITICA"],
            weights=[0.30, 0.40, 0.20, 0.10],
        )[0]
        ts = _fake.date_time_between(
            start_date=since,
            end_date="now",
            tzinfo=timezone.utc,
        )
        eventos.append(
            {
                "evento_id": str(uuid.uuid4()),
                "coordenadas": {"latitud": lat, "longitud": lon},
                "categoria": random.choice(_CATEGORIAS_MINERVA),
                "prioridad": prioridad,
                "timestamp_utc": ts.isoformat(),
                "meta": {
                    "zona": _fake.city(),
                    "unidad_reportante": _fake.numerify("UN-###"),
                    "descripcion": _fake.sentence(nb_words=10),
                },
            }
        )
    return {
        "total": total,
        "pagina": pagina,
        "por_pagina": _PAGE_SIZE,
        "eventos": eventos,
    }


class MinervaExtractor:
    """
    Extractor de incidentes desde el sistema MINERVA (API REST paginada).

    En producción, los requests se realizan con httpx contra el endpoint
    configurado en settings.MINERVA_DB_URL.  La implementación actual
    simula las respuestas paginadas de la API.

    Ejemplo de uso
    --------------
    extractor = MinervaExtractor()
    incidentes: List[IncidenteRaw] = extractor.extract(since=datetime(...))
    """

    SISTEMA = "MINERVA"

    def __init__(self) -> None:
        self._log = log.bind(extractor="MinervaExtractor")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def extract(self, since: datetime) -> List[IncidenteRaw]:
        """
        Extrae eventos de MINERVA creados a partir de `since`.

        Itera sobre todas las páginas disponibles hasta consumir el total.

        Parámetros
        ----------
        since : datetime
            Marca temporal (UTC) a partir de la cual buscar registros nuevos.

        Retorna
        -------
        List[IncidenteRaw]
            Eventos validados y listos para normalización.
        """
        self._log.info("minerva_extract_start", since=since.isoformat())

        incidentes: List[IncidenteRaw] = []
        errores = 0
        total_eventos_api = 0

        # Determinar total simulado una sola vez para consistencia de paginación
        total = random.randint(15, 120)
        total_paginas = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

        for pagina in range(1, total_paginas + 1):
            page_data = self._fetch_page(since, pagina, total)
            total_eventos_api = page_data.get("total", 0)
            eventos = page_data.get("eventos", [])

            self._log.debug(
                "minerva_page_fetched",
                pagina=pagina,
                total_paginas=total_paginas,
                eventos_en_pagina=len(eventos),
            )

            for evento in eventos:
                try:
                    incidente = self._map_evento(evento)
                    incidentes.append(incidente)
                except Exception as exc:  # noqa: BLE001
                    errores += 1
                    self._log.warning(
                        "minerva_evento_mapping_error",
                        evento_id=evento.get("evento_id"),
                        error=str(exc),
                    )

        self._log.info(
            "minerva_extract_complete",
            total_api=total_eventos_api,
            mapped=len(incidentes),
            errores=errores,
        )
        return incidentes

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _fetch_page(
        self,
        since: datetime,
        pagina: int,
        total: int,
    ) -> Dict[str, Any]:
        """
        Simula el request HTTP:
            GET /eventos?desde=<since>&pagina=<pagina>&por_pagina=<PAGE_SIZE>

        En producción usaría httpx:
            response = httpx.get(
                f"{settings.MINERVA_DB_URL}/eventos",
                params={"desde": since.isoformat(), "pagina": pagina, "por_pagina": PAGE_SIZE},
                headers={"Authorization": f"Bearer {settings.JUPITER_API_KEY}"},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        """
        return _simulate_minerva_page(since, pagina, total)

    def _map_evento(self, evento: Dict[str, Any]) -> IncidenteRaw:
        """
        Convierte un evento de la API de MINERVA al modelo IncidenteRaw.

        Conversiones clave:
        - coordenadas.latitud / coordenadas.longitud → latitud / longitud
        - prioridad (BAJA/MEDIA/ALTA/CRITICA) → severidad (1-5)
        - categoria → tipo_incidente
        - timestamp_utc → timestamp
        """
        coords = evento.get("coordenadas", {})
        prioridad = str(evento.get("prioridad", "MEDIA")).upper()

        return IncidenteRaw(
            id_sistema_origen=str(evento["evento_id"]),
            sistema_origen=self.SISTEMA,
            tipo_incidente=str(evento.get("categoria", "DESCONOCIDO")),
            severidad=_prioridad_to_severidad(prioridad),
            timestamp=str(evento["timestamp_utc"]),
            latitud=float(coords["latitud"]),
            longitud=float(coords["longitud"]),
            metadata={
                "prioridad_original": prioridad,
                **evento.get("meta", {}),
            },
        )
