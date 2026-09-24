"""Búsqueda + comentarios top de TikTok (Fase 4 del scraper local).

Por candidato: capturas de la página de búsqueda (cards con descripción visible).
Si el candidato está en cfg_red["candidatos_comentarios"], además abre los primeros
`videos_comentarios` videos y captura el panel de comentarios scrolleándolo — ahí
está la opinión de la audiencia. Fallas por video degradan a warning (la búsqueda
de ese candidato ya quedó capturada); challenge/captcha/redirect a login lanzan
SesionInvalidaError y run.py decide la rotación de cuenta.

Los selectores REALES se tunean en la PC de la oficina (marcados TUNEAR)."""
import urllib.parse
from pathlib import Path

import browser

TIKTOK_SEARCH_URL = "https://www.tiktok.com/search?q={q}"
LOGIN_URL = "https://www.tiktok.com/login"
# TUNEAR: DOM real de TikTok en la PC de la oficina (data-e2e suele ser lo más estable).
SELECTOR_RESULTADOS = "[data-e2e='search_top-item']"     # cards de video en la búsqueda
SELECTOR_LINKS_VIDEO = "a[href*='/video/']"              # links a videos dentro de las cards
SELECTOR_COMENTARIOS = "[data-e2e='comment-list']"       # panel de comentarios del video
SELECTOR_CAPTCHA = "[id*='captcha'], [class*='captcha']"  # overlay del captcha-puzzle
# URLs a las que TikTok redirige cuando exige login.
MARCAS_SESION_MUERTA = ("/login", "/signup")


def login_completado(url: str) -> bool:
    """TikTok sale de /login al terminar (típicamente redirige a /foryou)."""
    return "/login" not in url and "/signup" not in url


def url_busqueda(termino: str) -> str:
    return TIKTOK_SEARCH_URL.format(q=urllib.parse.quote(termino))


def links_de_videos(hrefs: list[str], cantidad: int) -> list[str]:
    """Primeros `cantidad` links de video únicos, en orden de aparición (pura)."""
    out: list[str] = []
    for h in hrefs:
        if "/video/" in h and h not in out:
            out.append(h)
        if len(out) == cantidad:
            break
    return out


def _verificar_sesion(page) -> None:
    if any(marca in page.url for marca in MARCAS_SESION_MUERTA):
        raise browser.SesionInvalidaError(f"redirigido a {page.url}")
    if page.locator(SELECTOR_CAPTCHA).count() > 0:
        raise browser.SesionInvalidaError("captcha de TikTok en pantalla")


def _capturar_comentarios(page, termino: str, video_url: str, cfg: dict, cfg_red: dict,
                          carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]:
    """Capturas del panel de comentarios de UN video. Fallas que no son de sesión
    degradan a warning y devuelven [] (la corrida sigue)."""
    esperas = tuple(cfg["espera_entre_scrolls"])
    try:
        page.goto(video_url, timeout=browser.TIMEOUT_FEED_MS)
        _verificar_sesion(page)
        page.wait_for_selector(SELECTOR_COMENTARIOS, timeout=browser.TIMEOUT_FEED_MS)
        browser.esperar_aleatorio(esperas)
        rutas = browser.capturar_elemento(page, SELECTOR_COMENTARIOS,
                                          cfg_red["scrolls_comentarios"],
                                          esperas, carpeta, prefijo)
    except browser.SesionInvalidaError:
        raise
    except Exception as e:
        warnings.append(f"TikTok: comentarios de un video de '{termino}' sin capturar ({e}).")
        return []
    contexto = (f"panel de comentarios de un video de TikTok sobre {termino}; "
                "cada comentario visible cuenta como una publicación")
    return [{"ruta": r, "contexto": contexto} for r in rutas]


def capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict,
             carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]:
    from playwright.sync_api import TimeoutError as PWTimeout  # diferido
    esperas = tuple(cfg["espera_entre_scrolls"])
    with browser.pagina_con_sesion(sesion, tuple(cfg["viewport"]), cfg["headless"]) as page:
        page.goto(url_busqueda(termino), timeout=browser.TIMEOUT_FEED_MS)
        _verificar_sesion(page)
        try:
            page.wait_for_selector(SELECTOR_RESULTADOS, timeout=browser.TIMEOUT_FEED_MS)
        except PWTimeout:
            raise browser.SesionInvalidaError(
                "los resultados no aparecieron (¿captcha o sesión vencida?)")
        browser.esperar_aleatorio(esperas)
        capturas = [{"ruta": r, "contexto": ""} for r in browser.capturar_pagina(
            page, cfg_red["scrolls_por_candidato"], esperas, carpeta, prefijo)]
        if termino in (cfg_red.get("candidatos_comentarios") or []):
            hrefs = page.eval_on_selector_all(SELECTOR_LINKS_VIDEO, "els => els.map(e => e.href)")
            for i, link in enumerate(links_de_videos(hrefs, cfg_red["videos_comentarios"])):
                capturas += _capturar_comentarios(
                    page, termino, link, cfg, cfg_red, carpeta,
                    f"{prefijo}-comentarios-{i + 1}", warnings)
    return capturas
