"""Búsqueda de X (pestaña Recientes, lang:es) — movida de browser.py en la Fase 4.

Los selectores/tiempos REALES se tunean en la PC de la oficina."""
import urllib.parse
from pathlib import Path

import browser

X_SEARCH_URL = "https://x.com/search?q={q}&src=typed_query&f=live"
# El feed de X renderiza cada post como <article>. Si X cambia, tunear acá.
SELECTOR_FEED = "article"
# URLs a las que X redirige cuando la sesión no sirve o hay challenge.
MARCAS_SESION_MUERTA = ("/login", "/account/access", "/i/flow")

LOGIN_URL = "https://x.com/login"


def login_completado(url: str) -> bool:
    """X redirige a /home al terminar el login manual."""
    return "/home" in url


def url_busqueda(termino: str) -> str:
    q = urllib.parse.quote(f"{termino} lang:es")
    return X_SEARCH_URL.format(q=q)


def capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict,
             carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]:
    """Capturas de la búsqueda de X. En X no hay contexto especial: todas las
    capturas son del feed de búsqueda (contexto == "")."""
    from playwright.sync_api import TimeoutError as PWTimeout  # diferido
    esperas = tuple(cfg["espera_entre_scrolls"])
    with browser.pagina_con_sesion(sesion, tuple(cfg["viewport"]), cfg["headless"]) as page:
        page.goto(url_busqueda(termino), timeout=browser.TIMEOUT_FEED_MS)
        if any(marca in page.url for marca in MARCAS_SESION_MUERTA):
            raise browser.SesionInvalidaError(f"redirigido a {page.url}")
        try:
            page.wait_for_selector(SELECTOR_FEED, timeout=browser.TIMEOUT_FEED_MS)
        except PWTimeout:
            raise browser.SesionInvalidaError("el feed no apareció (¿challenge o sesión vencida?)")
        browser.esperar_aleatorio(esperas)  # dejar asentar el feed antes de la primera captura
        rutas = browser.capturar_pagina(page, cfg_red["scrolls_por_candidato"],
                                        esperas, carpeta, prefijo)
    return [{"ruta": r, "contexto": ""} for r in rutas]
