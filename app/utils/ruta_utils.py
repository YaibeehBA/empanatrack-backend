from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from math import atan2, cos, radians, sin, sqrt

import httpx

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

OSRM_BASE            = "https://routing.openstreetmap.de/routed-foot"
TIMEOUT_TABLE        = 10.0
TIMEOUT_SEGMENT      = 12.0
MAX_RETRIES          = 3
RETRY_BACKOFF        = [1, 2, 4]
MAX_EMPRESAS_TABLE   = 25   # límite waypoints OSRM Table público
MAX_PARALELO         = 8    # máx requests simultáneos a OSRM
VELOCIDAD_MS         = 1.39  # 5 km/h en m/s

# ─────────────────────────────────────────────────────────────────────────────
# TIPOS INTERNOS
# ─────────────────────────────────────────────────────────────────────────────

Coord = tuple[float, float]   # (lat, lng)


@dataclass
class SegmentoRuta:
    puntos:  list[tuple[float, float]]   # lista de (lat, lng)
    fuente:  str                          # "osrm" | "haversine"
    distancia: float                      # metros


@dataclass
class ResultadoRuta:
    orden:            list[int]
    segmentos:        list[SegmentoRuta]
    fuente_orden:     str                 # "osrm" | "haversine"
    fuente_segmentos: str                 # "osrm" | "haversine" | "mixto"
    distancia_total:  float
    tiempo_minutos:   float


# ─────────────────────────────────────────────────────────────────────────────
# GEOMETRÍA
# ─────────────────────────────────────────────────────────────────────────────

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distancia en metros entre dos coordenadas."""
    R = 6_371_000
    la1, lo1 = radians(lat1), radians(lng1)
    la2, lo2 = radians(lat2), radians(lng2)
    dlat = la2 - la1
    dlon = lo2 - lo1
    h = sin(dlat / 2) ** 2 + cos(la1) * cos(la2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(h), sqrt(1 - h))


def distancia_total_puntos(puntos: list[tuple[float, float]]) -> float:
    """Suma de haversine entre puntos consecutivos."""
    return sum(
        haversine(puntos[i][0], puntos[i][1], puntos[i+1][0], puntos[i+1][1])
        for i in range(len(puntos) - 1)
    )


# ─────────────────────────────────────────────────────────────────────────────
# ALGORITMO DE ORDEN
# ─────────────────────────────────────────────────────────────────────────────

def _mejor_orden_desde_matriz(matriz: list[list[float]]) -> list[int]:
    """
    Nearest-neighbor evaluando todos los posibles inicios.
    Devuelve el orden que minimiza el costo total.
    """
    n = len(matriz)
    mejor_costo = float("inf")
    mejor:  list[int] = list(range(n))

    for inicio in range(n):
        visitados = [False] * n
        visitados[inicio] = True
        orden  = [inicio]
        actual = inicio
        costo  = 0.0

        for _ in range(n - 1):
            sig   = min(
                (j for j in range(n) if not visitados[j]),
                key=lambda j: matriz[actual][j],
                default=None,
            )
            if sig is None:
                break
            visitados[sig] = True
            costo += matriz[actual][sig]
            orden.append(sig)
            actual = sig

        if costo < mejor_costo:
            mejor_costo = costo
            mejor       = orden

    return mejor


def _orden_haversine(coords: list[Coord]) -> list[int]:
    """Fallback: matriz haversine + nearest-neighbor."""
    n      = len(coords)
    matriz = [
        [haversine(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
         for j in range(n)]
        for i in range(n)
    ]
    return _mejor_orden_desde_matriz(matriz)


# ─────────────────────────────────────────────────────────────────────────────
# OSRM — TABLA
# ─────────────────────────────────────────────────────────────────────────────

async def _osrm_table(
    client: httpx.AsyncClient,
    coords: list[Coord],
) -> list[list[float]] | None:
    coords_str = ";".join(f"{lng},{lat}" for lat, lng in coords)
    url = f"{OSRM_BASE}/table/v1/foot/{coords_str}?annotations=duration"

    try:
        resp = await client.get(url, timeout=TIMEOUT_TABLE)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and "durations" in data:
                return [
                    [float(v) if v is not None else 9_999.0 for v in row]
                    for row in data["durations"]
                ]
    except Exception as e:
        logger.warning("OSRM Table falló: %s", e)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# OSRM — SEGMENTO
# ─────────────────────────────────────────────────────────────────────────────

async def _osrm_segmento(
    client:    httpx.AsyncClient,
    semaforo:  asyncio.Semaphore,
    origen:    Coord,
    destino:   Coord,
) -> SegmentoRuta:
    """
    Traza un segmento origen→destino por calles reales.
    Reintenta MAX_RETRIES veces con backoff.
    Si todos fallan, devuelve línea recta para ese segmento.
    """
    lat1, lng1 = origen
    lat2, lng2 = destino
    url = (
        f"{OSRM_BASE}/route/v1/foot/"
        f"{lng1},{lat1};{lng2},{lat2}"
        f"?overview=full&geometries=geojson&steps=false"
    )

    async with semaforo:
        for intento in range(MAX_RETRIES):
            try:
                resp = await client.get(url, timeout=TIMEOUT_SEGMENT)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == "Ok" and data.get("routes"):
                        raw = data["routes"][0]["geometry"]["coordinates"]
                        # OSRM devuelve [lng, lat] → invertimos
                        puntos = [(float(c[1]), float(c[0])) for c in raw]
                        return SegmentoRuta(
                            puntos=    puntos,
                            fuente=    "osrm",
                            distancia= distancia_total_puntos(puntos),
                        )

                logger.warning(
                    "OSRM Route HTTP %d intento %d/%d — %s→%s",
                    resp.status_code, intento + 1, MAX_RETRIES, origen, destino,
                )

            except httpx.TimeoutException:
                logger.warning(
                    "OSRM Route timeout intento %d/%d — %s→%s",
                    intento + 1, MAX_RETRIES, origen, destino,
                )
            except Exception as e:
                logger.warning(
                    "OSRM Route error intento %d/%d — %s→%s: %s",
                    intento + 1, MAX_RETRIES, origen, destino, e,
                )

            if intento < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[intento])

    # Fallback línea recta solo para este segmento
    logger.warning(
        "Segmento %s→%s en línea recta (OSRM falló %d intentos)",
        origen, destino, MAX_RETRIES,
    )
    puntos = [origen, destino]
    return SegmentoRuta(
        puntos=    puntos,
        fuente=    "haversine",
        distancia= haversine(lat1, lng1, lat2, lng2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# FUNCIÓN PÚBLICA — única que importa el router
# ─────────────────────────────────────────────────────────────────────────────

async def calcular_orden_y_polilinea(
    coords: list[Coord],
) -> ResultadoRuta:
    """
    Dado una lista de coordenadas (lat, lng):
      1. Calcula el orden óptimo de visita
      2. Traza la polilínea por calles reales segmento a segmento
      3. Devuelve ResultadoRuta con todo lo necesario para construir la respuesta

    El router no necesita saber nada de OSRM, haversine ni asyncio.
    """
    n = len(coords)

    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)

    async with httpx.AsyncClient(limits=limits) as client:

        # ── Paso 1: orden óptimo ──────────────────────────────────────────────
        fuente_orden = "osrm"

        if n <= MAX_EMPRESAS_TABLE:
            matriz = await _osrm_table(client, coords)
        else:
            logger.info(
                "%d empresas > límite %d — usando haversine para el orden",
                n, MAX_EMPRESAS_TABLE,
            )
            matriz = None

        if matriz:
            orden = _mejor_orden_desde_matriz(matriz)
        else:
            orden        = _orden_haversine(coords)
            fuente_orden = "haversine"

        coords_ord = [coords[i] for i in orden]

        # ── Paso 2: segmentos en paralelo ─────────────────────────────────────
        semaforo = asyncio.Semaphore(MAX_PARALELO)

        tareas = [
            _osrm_segmento(client, semaforo, coords_ord[i], coords_ord[i + 1])
            for i in range(n - 1)
        ]
        segmentos: list[SegmentoRuta] = await asyncio.gather(*tareas)

    # ── Paso 3: métricas globales ─────────────────────────────────────────────
    fuentes       = {s.fuente for s in segmentos}
    fuente_segs   = (
        "osrm"      if fuentes == {"osrm"}      else
        "haversine" if fuentes == {"haversine"} else
        "mixto"
    )

    if fuente_segs != "osrm":
        malos = [i for i, s in enumerate(segmentos) if s.fuente != "osrm"]
        logger.warning("Segmentos en línea recta: %s", malos)

    distancia_total = sum(s.distancia for s in segmentos)
    tiempo_minutos  = (distancia_total / VELOCIDAD_MS) / 60

    return ResultadoRuta(
        orden=            orden,
        segmentos=        segmentos,
        fuente_orden=     fuente_orden,
        fuente_segmentos= fuente_segs,
        distancia_total=  distancia_total,
        tiempo_minutos=   tiempo_minutos,
    )