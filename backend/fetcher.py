"""
Capa 2 — Extracción / Fetcher (CIEGO).

Única responsabilidad: traer resultados crudos de Google vía SerpAPI, por red.
NO sabe nada de Gemini, scoring, normalización ni del shape final de los posts:
solo devuelve dicts crudos {title, snippet, url, date}.

Si cambia la conexión a la API externa o los parámetros de búsqueda -> SOLO acá.
"""
import os
import re
import datetime
import requests

# ── Configuración de SerpAPI ──────────────────────────────────────────────────
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
SERPAPI_URL = "https://serpapi.com/search"

# ── Redes soportadas ──────────────────────────────────────────────────────────
SUPPORTED_NETWORKS = ("twitter", "instagram", "tiktok")

# ── Geolocalización (parámetro 'gl' de SerpAPI) ───────────────────────────────
# El frontend manda el país como código ISO 3166-1 alpha-2 (ar, br, us, es, ...),
# que es exactamente el formato que espera 'gl'. Por eso NO mantenemos una lista
# hardcodeada de países: cualquier código ISO válido funciona como gl.
#
# 'hl' (idioma de la interfaz de Google) sí conviene alinearlo al país para traer
# resultados nativos de esa región. Solo mapeamos los idiomas más comunes; para
# cualquier país no listado caemos a "es" (el operador lee español y, además, la
# Capa 3 traduce al español todo lo que venga en otro idioma — ver B.5).
_DEFAULT_GL = "ar"
_HL_BY_GL = {
    "ar": "es", "es": "es", "mx": "es", "cl": "es", "uy": "es", "co": "es",
    "pe": "es", "ve": "es", "bo": "es", "py": "es", "ec": "es",
    "br": "pt", "pt": "pt",
    "us": "en", "gb": "en", "au": "en", "ca": "en", "ie": "en", "nz": "en",
    "fr": "fr", "it": "it", "de": "de", "jp": "ja", "cn": "zh-cn",
}


def _geo_params(country: str | None) -> tuple[str, str]:
    """Devuelve (gl, hl) a partir del código ISO de país. gl = el código tal cual;
    hl = idioma alineado al país (default 'es')."""
    gl = (country or _DEFAULT_GL).strip().lower() or _DEFAULT_GL
    hl = _HL_BY_GL.get(gl, "es")
    return gl, hl

_SITE_BY_NETWORK = {
    "twitter": "site:x.com OR site:twitter.com",
    "instagram": "site:instagram.com",
    "tiktok": "site:tiktok.com",
}


def _date_to_qdr(fecha_desde: str | None) -> str | None:
    """Mapea YYYY-MM-DD a un valor qdr de SerpAPI (d/w/m) según antigüedad."""
    if not fecha_desde:
        return None
    try:
        d = datetime.date.fromisoformat(fecha_desde)
    except ValueError:
        return None
    delta_days = (datetime.date.today() - d).days
    if delta_days <= 0:
        return "d"
    if delta_days <= 7:
        return "w"
    if delta_days <= 31:
        return "m"
    return None


# ── C.1 · Hard-stop de fechas (best-effort) para IG/TikTok ────────────────────
# SerpAPI (Google) NO scrapea IG/TikTok directo: devuelve un campo "date" que
# suele venir vacío o en formato relativo ("hace 3 meses", "2 months ago") y a
# veces absoluto ("Jun 2, 2024", "2024-06-02"). Parseamos lo que se pueda y
# destruimos SOLO los posts que confirmamos anteriores a la fecha pedida. Los de
# fecha vacía/no parseable se conservan para no vaciar la grilla.
_SPANISH_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}
_ENGLISH_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun",
                   "jul", "aug", "sep", "oct", "nov", "dec"]
_REL_UNIT_DAYS = {
    "dia": 1, "día": 1, "dias": 1, "días": 1, "day": 1, "days": 1,
    "semana": 7, "semanas": 7, "week": 7, "weeks": 7,
    "mes": 30, "meses": 30, "month": 30, "months": 30,
    "ano": 365, "año": 365, "anos": 365, "años": 365, "year": 365, "years": 365,
}


def _parse_result_date(raw: str) -> datetime.date | None:
    """Best-effort: convierte el 'date' de SerpAPI a date. None si no se puede."""
    if not raw:
        return None
    s = raw.strip().lower()
    today = datetime.date.today()
    if s in ("hoy", "today"):
        return today
    if s in ("ayer", "yesterday"):
        return today - datetime.timedelta(days=1)
    # Relativas: "hace 3 meses", "hace un mes", "3 months ago", "a week ago".
    if "hace" in s or "ago" in s:
        m = re.search(r"(\d+|un|una|a)\s+([a-záéíóúñ]+)", s)
        if m:
            qty_raw = m.group(1)
            qty = 1 if qty_raw in ("un", "una", "a") else int(qty_raw)
            unit = m.group(2)
            days = _REL_UNIT_DAYS.get(unit) or _REL_UNIT_DAYS.get(unit.rstrip("s"))
            if days:
                return today - datetime.timedelta(days=qty * days)
    # ISO 2024-06-02
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # dd/mm/yyyy (locale AR; si el primer número > 12 lo tratamos como día)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s)
    if m:
        d_, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mth > 12 and d_ <= 12:
            d_, mth = mth, d_
        try:
            return datetime.date(y, mth, d_)
        except ValueError:
            return None
    # "jun 2, 2024" / "2 jun 2024" / "jun 2024" (mes abreviado ES o EN)
    month = next((num for name, num in _SPANISH_MONTHS.items() if re.search(rf"\b{name}", s)), None)
    if month is None:
        month = next((i for i, name in enumerate(_ENGLISH_MONTHS, 1) if re.search(rf"\b{name}", s)), None)
    if month is not None:
        ym = re.search(r"\b(\d{4})\b", s)
        if ym:
            year = int(ym.group(1))
            # primer número de 1-2 dígitos = día (si existe); si no, día 1.
            day = next((int(x) for x in re.findall(r"\b(\d{1,2})\b", s)), 1)
            try:
                return datetime.date(year, month, day)
            except ValueError:
                return None
    return None


def _filter_results_by_date(items: list[dict], fecha_desde: str, network: str) -> list[dict]:
    """Destruye los items con fecha parseable ANTERIOR a fecha_desde. Conserva los
    de fecha vacía/no parseable (best-effort, ver C.1)."""
    try:
        floor = datetime.date.fromisoformat(fecha_desde)
    except (ValueError, TypeError):
        return items
    kept: list[dict] = []
    dropped = 0
    for it in items:
        d = _parse_result_date(it.get("date", ""))
        if d is not None and d < floor:
            dropped += 1
            continue
        kept.append(it)
    if dropped:
        print(f"DEBUG hard-stop fecha[{network}]: {dropped} posts anteriores a {fecha_desde} destruidos")
    return kept


def _build_accounts_filter(accounts: list[str] | None) -> str:
    """'(from:user1 OR from:user2)' — operador específico de X/Twitter."""
    if not accounts:
        return ""
    clean = [a.lstrip("@").strip() for a in accounts if a and a.strip()]
    if not clean:
        return ""
    return "(" + " OR ".join(f"from:{u}" for u in clean) + ")"


def _clean_serp_results(items: list[dict]) -> list[dict]:
    """
    Limpia los resultados crudos de SerpAPI antes de devolverlos:
    - descarta items sin snippet ni title (no hay texto que analizar)
    - dedupea por URL exacta
    - dedupea por snippet exacto (distinto URL, mismo contenido)
    Reduce tokens del prompt aguas abajo y baja la chance de tocar el rate limit.
    """
    seen_urls: set[str] = set()
    seen_text: set[str] = set()
    out: list[dict] = []
    for it in items:
        url = (it.get("url") or "").strip()
        snippet = (it.get("snippet") or "").strip()
        title = (it.get("title") or "").strip()
        if not snippet and not title:
            continue
        if url and url in seen_urls:
            continue
        text_key = snippet or title
        if text_key in seen_text:
            continue
        if url:
            seen_urls.add(url)
        seen_text.add(text_key)
        out.append(it)
    return out


def _serpapi_get_with_geo_fallback(params: dict, network: str) -> dict | None:
    """Pega a SerpAPI con degradación geográfica progresiva. Si la petición con los
    parámetros de región falla (timeout / 4xx-5xx de SerpAPI por combinación de
    región/idioma), reintenta quitando 'hl' y luego 'gl'+'hl', en vez de romper.
    Así una búsqueda internacional nunca deja al backend sin respuesta por culpa de
    los parámetros geográficos. Devuelve el JSON o None si TODO falló."""
    intentos = [("región completa", params)]
    if "hl" in params or "gl" in params:
        sin_hl = {k: v for k, v in params.items() if k != "hl"}
        intentos.append(("sin hl", sin_hl))
        sin_geo = {k: v for k, v in sin_hl.items() if k != "gl"}
        intentos.append(("sin gl/hl", sin_geo))

    for i, (etiqueta, p) in enumerate(intentos):
        try:
            response = requests.get(SERPAPI_URL, params=p, timeout=30)
            response.raise_for_status()
            data = response.json()
            if i > 0:
                print(f"DEBUG: SerpAPI[{network}] recuperado en fallback geo '{etiqueta}'.")
            return data
        except requests.exceptions.RequestException as e:
            print(f"ERROR: SerpAPI[{network}] intento '{etiqueta}' falló: {e}")
            continue
    return None


def search_serpapi(
    termino: str,
    network: str = "twitter",
    max_results: int = 10,
    fecha_desde: str | None = None,
    accounts: list[str] | None = None,
    country: str = "ar",
) -> list[dict] | None:
    """
    Busca en Google vía SerpAPI con `site:` correspondiente a la red.
    `country` es el código ISO 3166-1 alpha-2 que se pasa como 'gl' para forzar
    resultados nativos de esa región (B.4).
    Devuelve lista de dicts {title, snippet, url, date}, [] si no hay resultados,
    o None ante errores de red/upstream (para no cachear vacíos espurios).
    """
    if not SERPAPI_API_KEY:
        print("ERROR: SERPAPI_API_KEY no configurada en las variables de entorno.")
        return None

    site_filter = _SITE_BY_NETWORK.get(network, _SITE_BY_NETWORK["twitter"])
    query_parts = [site_filter, termino]
    # (from:user) es operador propio de X — IG/TikTok no tienen equivalente directo
    # vía Google search, así que para esas redes ignoramos el filtro por cuenta.
    if network == "twitter":
        accounts_filter = _build_accounts_filter(accounts)
        if accounts_filter:
            query_parts.append(accounts_filter)
    query = " ".join(query_parts)
    gl, hl = _geo_params(country)
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_API_KEY,
        "num": max_results,
        "hl": hl,
        "gl": gl,
    }
    qdr = _date_to_qdr(fecha_desde)
    if qdr:
        params["tbs"] = f"qdr:{qdr}"

    data = _serpapi_get_with_geo_fallback(params, network)
    if data is None:
        return None

    organic_results = data.get("organic_results", [])
    if not organic_results:
        print(f"DEBUG: SerpAPI[{network}] sin resultados orgánicos para '{termino}'.")
        return []

    raw_results = []
    for item in organic_results[:max_results]:
        raw_results.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": item.get("link", ""),
            "date": item.get("date", ""),
        })
    results = _clean_serp_results(raw_results)
    if len(results) != len(raw_results):
        print(f"DEBUG: SerpAPI[{network}] limpieza: {len(raw_results)} -> {len(results)} (vacíos/duplicados descartados)")
    # C.1 · Hard-stop de fechas best-effort para IG/TikTok: el qdr de SerpAPI es
    # grueso (día/semana/mes) y deja colar posts viejos (p. ej. 2024). Acá los
    # destruimos en código si la fecha parseable es anterior a la pedida.
    if network in ("instagram", "tiktok") and fecha_desde:
        results = _filter_results_by_date(results, fecha_desde, network)
    print(f"DEBUG: SerpAPI[{network}] devolvió {len(results)} resultados útiles para '{termino}'.")
    return results


def search_serpapi_web(
    query: str,
    max_results: int = 5,
    country: str = "ar",
) -> list[dict] | None:
    """Búsqueda web GENERAL en Google vía SerpAPI (SIN filtro site:), para traer
    notas/artículos (p. ej. encuestas de consultoras). Devuelve lista de dicts
    {title, snippet, url, date}, [] si no hay resultados, o None ante error de
    red/upstream (para no cachear vacíos espurios)."""
    if not SERPAPI_API_KEY:
        print("ERROR: SERPAPI_API_KEY no configurada en las variables de entorno.")
        return None
    gl, hl = _geo_params(country)
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_API_KEY,
        "num": max_results,
        "hl": hl,
        "gl": gl,
    }
    data = _serpapi_get_with_geo_fallback(params, "web")
    if data is None:
        return None
    organic_results = data.get("organic_results", [])
    if not organic_results:
        print(f"DEBUG: SerpAPI[web] sin resultados orgánicos para '{query}'.")
        return []
    out: list[dict] = []
    for item in organic_results[:max_results]:
        out.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": item.get("link", ""),
            "date": item.get("date", ""),
        })
    return out
