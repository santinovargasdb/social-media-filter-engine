"""
Capa de SCRAPING para la Boca de Urna (alternativa a SerpAPI).

Interfaz común `scrape_network(...)` con la MISMA firma y forma de retorno que
`normalizer.fetch_raw_posts` (`(posts, hubo_upstream)`, cada post con el shape de
`_normalize_raw_post`), para que `electoral.run_network_block` la use detrás de un
feature flag SIN tocar la clasificación de Gemini, el desglose por_red, el async ni
el frontend.

Por qué API de terceros y no scraping propio: un navegador headless (Playwright)
NO entra en el Render free-tier (RAM) y las libs no oficiales (twscrape/instaloader/
TikTokApi) exigen proxies residenciales + cuentas + mantenimiento constante y no son
gratis "en serio". Una API de scraping HTTP de terceros resuelve anti-bot/proxies/
sesiones del lado del proveedor, corre con solo `requests` (ya en requirements), y
arranca con trial gratis. Se accede a contenido PÚBLICO (sin login) para minimizar
el riesgo de ToS.

Estado: X implementado (twitterapi.io). Instagram y TikTok quedan para Fase 2/3
(hoy levantan NotImplementedError; el dispatcher del flag cae a SerpAPI para esas
redes mientras tanto).

Variables de entorno:
- X_SCRAPER_API_KEY: API key del proveedor de X (trial gratis, sin tarjeta).
"""
import os
import time

import requests

import normalizer

X_SCRAPER_API_KEY = os.environ.get("X_SCRAPER_API_KEY", "")

# La API tira 429 ante ráfagas (el análisis dispara ~14 búsquedas seguidas). Como el
# análisis es async (sin apuro de 120s), reintentamos con backoff en vez de perder la
# búsqueda. Editable.
_X_MAX_RETRIES = 4
_X_BACKOFF = 2.0  # seg base (2, 4, 6… por reintento)

# Endpoint de búsqueda avanzada de twitterapi.io (acepta la sintaxis de búsqueda
# avanzada de X). VERIFICAR endpoint/campos/headers exactos contra la API real al
# conectar la key: el acceso HTTP está aislado en `_x_http` y el parseo en
# `_tweet_to_src`, así cualquier ajuste es local y testeable con fixtures.
_X_API_URL = "https://api.twitterapi.io/twitter/tweet/advanced_search"
_X_TIMEOUT = 30


def scrape_network(network: str, termino: str, fecha_desde: str | None = None,
                   keywords: list[str] | None = None, accounts: list[str] | None = None,
                   country: str = "ar", pages: int = 1) -> tuple[list[dict], bool]:
    """Trae posts públicos de UNA red vía scraping de terceros. Misma firma y retorno
    que `normalizer.fetch_raw_posts`: `(posts, hubo_upstream)`."""
    if network == "twitter":
        return _scrape_x(termino, fecha_desde, keywords or [], pages)
    if network == "instagram":
        raise NotImplementedError("scraper de Instagram: Fase 2")
    if network == "tiktok":
        raise NotImplementedError("scraper de TikTok: Fase 3")
    return [], False


# ── X / Twitter (twitterapi.io) ───────────────────────────────────────────────
def _x_query(termino: str, fecha_desde: str | None) -> str:
    """Arma la query de búsqueda avanzada de X. Sesga a español (lang:es) para
    contenido argentino; `since:` acota por fecha desde."""
    q = f"{termino.strip()} lang:es".strip()
    if fecha_desde:
        q = f"{q} since:{fecha_desde}".strip()
    return q


def _x_http(query: str, cursor: str | None) -> tuple[dict | None, bool]:
    """UN request a la API de X. Devuelve (json, ok). Aislado a propósito para poder
    testear el parseo con fixtures sin tocar la red."""
    if not X_SCRAPER_API_KEY:
        print("ERROR: X_SCRAPER_API_KEY no configurada en las variables de entorno.")
        return None, False
    params = {"query": query, "queryType": "Latest"}
    if cursor:
        params["cursor"] = cursor
    for intento in range(_X_MAX_RETRIES):
        try:
            resp = requests.get(_X_API_URL, params=params,
                                headers={"X-API-Key": X_SCRAPER_API_KEY}, timeout=_X_TIMEOUT)
            # 429 (rate-limit) o 5xx: transitorio -> backoff y reintento (el análisis
            # dispara muchas búsquedas seguidas y la API throttlea las ráfagas).
            if (resp.status_code == 429 or resp.status_code >= 500) and intento < _X_MAX_RETRIES - 1:
                time.sleep(_X_BACKOFF * (intento + 1))
                continue
            resp.raise_for_status()
            return resp.json(), True
        except requests.exceptions.RequestException as e:
            if intento < _X_MAX_RETRIES - 1:
                time.sleep(_X_BACKOFF * (intento + 1))
                continue
            print(f"ERROR X scraper: {e}")
            return None, False
    return None, False


def _tweet_to_src(tw: dict) -> dict:
    """Mapea un tweet de la API al 'src' crudo que consume `_normalize_raw_post`
    (mismo shape que un resultado de SerpAPI). VERIFICAR nombres de campo con la API
    real; están concentrados acá a propósito."""
    author = tw.get("author") or {}
    url = tw.get("url") or tw.get("twitterUrl") or ""
    if not url:  # reconstruir desde handle + id si la API no da URL directa
        handle = author.get("userName") or author.get("screen_name") or ""
        tid = tw.get("id") or tw.get("id_str") or ""
        if handle and tid:
            url = f"https://x.com/{handle}/status/{tid}"
    return {
        "url": url,
        "snippet": tw.get("text") or tw.get("full_text") or "",
        "title": "",
        "date": tw.get("createdAt") or tw.get("created_at") or "",
        "network": "twitter",
    }


def _scrape_x(termino: str, fecha_desde: str | None, keywords: list[str],
              pages: int) -> tuple[list[dict], bool]:
    """Busca en X hasta `pages` páginas (paginación por cursor), normaliza cada tweet
    con `_normalize_raw_post` (reusa la limpieza de URL/autor del monitor) y dedupea
    por post_url. Devuelve ([], True) ante error de red en la 1ª página."""
    query = _x_query(termino, fecha_desde)
    posts: list[dict] = []
    seen: set[str] = set()
    cursor: str | None = None
    any_up = False
    for page in range(max(1, pages)):
        data, ok = _x_http(query, cursor)
        if not ok:
            any_up = True
            break
        tweets = data.get("tweets") or data.get("data") or []
        if not tweets:
            break
        for tw in tweets:
            post = normalizer._normalize_raw_post(_tweet_to_src(tw), termino, keywords)
            if post is None:
                continue
            key = post.get("post_url") or ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            posts.append(post)
        if not data.get("has_next_page"):
            break
        cursor = data.get("next_cursor") or ""
        if not cursor:
            break
    return posts, any_up
