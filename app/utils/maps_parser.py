import re
from urllib.error    import URLError
from urllib.request  import Request, urlopen
from urllib.parse    import parse_qs, unquote, urlparse, quote

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _fetch(url: str, timeout: int = 15):
    """Hace GET y retorna (html, url_final). Maneja encoding."""
    try:
        # Codificar caracteres no-ASCII en la URL
        safe_url = url.encode("ascii", errors="ignore").decode("ascii")
        # Si la URL se dañó, reconstruirla con quote
        if len(safe_url) < len(url) * 0.8:
            parsed   = urlparse(url)
            safe_url = parsed._replace(
                path=quote(parsed.path, safe="/:@!$&'()*+,;=-._~"),
            ).geturl()

        req = Request(safe_url, headers=_HEADERS)
        with urlopen(req, timeout=timeout) as r:
            final_url = r.geturl() or safe_url
            html      = r.read().decode("utf-8", "ignore")
            return html, final_url
    except URLError:
        return None, url
    except Exception:
        return None, url


def _resolve_short_url(url: str) -> str:
    parsed = urlparse(url)
    host   = parsed.netloc.lower()
    if "maps.app.goo.gl" not in host and host != "goo.gl":
        return url
    try:
        safe_url = url.encode("ascii", errors="ignore").decode("ascii")
        req = Request(safe_url, headers=_HEADERS)
        with urlopen(req, timeout=10) as r:
            return r.geturl() or url
    except Exception:
        return url


def _coords_from_html(html: str):
    """Extrae coordenadas del HTML de Google Maps."""
    if not html:
        return None, None

    patterns = [
        # !3d<lat>!4d<lng>
        (r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", False),
        # !2d<lng>!3d<lat>
        (r"!2d(-?\d+(?:\.\d+)?)!3d(-?\d+(?:\.\d+)?)", True),
        # [null,null,lat,lng]
        (r'\[\s*null\s*,\s*null\s*,\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\]',
         False),
        # @lat,lng con alta precisión
        (r'@(-?\d+\.\d{5,}),(-?\d+\.\d{5,})', False),
    ]

    for pattern, invertir in patterns:
        m = re.search(pattern, html)
        if m:
            try:
                v1 = float(m.group(1))
                v2 = float(m.group(2))
                lat, lng = (v2, v1) if invertir else (v1, v2)
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    return lat, lng
            except ValueError:
                continue

    return None, None


def _buscar_por_nombre(nombre: str):
    """
    Última opción: busca el lugar por nombre en Google Maps
    y extrae coordenadas de la URL final o del HTML.
    """
    # Codificar el nombre correctamente
    nombre_encoded = quote(nombre, safe="")
    search_url     = f"https://www.google.com/maps/search/{nombre_encoded}"

    html, final_url = _fetch(search_url)

    # Intentar extraer de la URL final (@lat,lng)
    m = re.search(r"@(-?\d+\.\d{4,}),(-?\d+\.\d{4,})", final_url or "")
    if m:
        lat = float(m.group(1))
        lng = float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng

    # Intentar extraer del HTML
    if html:
        return _coords_from_html(html)

    return None, None


def parse_google_maps_url(url: str) -> dict:
    if not url or not url.strip():
        raise ValueError("La URL no puede estar vacía.")

    # ── Resolver URL corta ────────────────────────────────
    url_resuelta = _resolve_short_url(url.strip())
    parsed       = urlparse(url_resuelta)

    if not parsed.scheme or not parsed.netloc:
        raise ValueError("URL inválida.")

    host = parsed.netloc.lower()
    if "google." not in host and "goo.gl" not in host:
        raise ValueError("La URL no parece ser de Google Maps.")

    full_url   = unquote(url_resuelta)
    path       = unquote(parsed.path or "")
    query      = parse_qs(parsed.query or "")
    latitud    = None
    longitud   = None
    place_name = None

    # ── Intento 1: !3dLAT!4dLNG en la URL ────────────────
    m = re.search(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)", full_url)
    if m:
        lat = float(m.group(1))
        lng = float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            latitud  = lat
            longitud = lng

    # ── Intento 2: !2dLNG!3dLAT en la URL ────────────────
    if latitud is None:
        m = re.search(
            r"!2d(-?\d+(?:\.\d+)?)!3d(-?\d+(?:\.\d+)?)", full_url)
        if m:
            lng = float(m.group(1))
            lat = float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                latitud  = lat
                longitud = lng

    # ── Intento 3: @lat,lng con alta precisión en la URL ──
    if latitud is None:
        m = re.search(r"@(-?\d+\.\d{4,}),(-?\d+\.\d{4,})", full_url)
        if m:
            lat = float(m.group(1))
            lng = float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                latitud  = lat
                longitud = lng

    # ── Intento 4: query param q=lat,lng ─────────────────
    if latitud is None:
        for param in ("q", "ll"):
            if param in query:
                m = re.search(
                    r"(-?\d+\.\d{4,})\s*,\s*(-?\d+\.\d{4,})",
                    query[param][0],
                )
                if m:
                    lat = float(m.group(1))
                    lng = float(m.group(2))
                    if -90 <= lat <= 90 and -180 <= lng <= 180:
                        latitud  = lat
                        longitud = lng
                        break

    # ── Intento 5: scraping del HTML de la URL resuelta ───
    if latitud is None:
        html, _ = _fetch(url_resuelta)
        if html:
            latitud, longitud = _coords_from_html(html)

    # ── Intento 6: buscar por nombre (caso ftid) ──────────
    # Google Maps a veces resuelve a ?q=nombre&ftid=...
    # sin coordenadas en la URL — buscamos por nombre
    if latitud is None:
        nombre_q = None
        if "q" in query:
            nombre_q = unquote(query["q"][0]).replace("+", " ").strip()
        elif place_name:
            nombre_q = place_name

        if nombre_q and not re.fullmatch(
            r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*", nombre_q
        ):
            latitud, longitud = _buscar_por_nombre(nombre_q)

    if latitud is None or longitud is None:
        raise ValueError(
            "No se pudieron extraer coordenadas. "
            "Prueba: abre Google Maps → busca el lugar → "
            "toca sobre el pin → usa 'Compartir'."
        )

    # ── Extraer nombre del lugar ──────────────────────────
    # Desde /place/NOMBRE/
    m = re.search(r"/place/([^/]+)", path)
    if m:
        raw        = unquote(m.group(1)).replace("+", " ").strip()
        place_name = raw.split("@")[0].strip() or None

    # Desde query param q= si no es solo coordenadas
    if not place_name and "q" in query:
        raw_q = unquote(query["q"][0]).replace("+", " ").strip()
        if not re.fullmatch(
            r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*", raw_q
        ):
            place_name = raw_q or None

    return {
        "latitud":    latitud,
        "longitud":   longitud,
        "place_name": place_name,
    }