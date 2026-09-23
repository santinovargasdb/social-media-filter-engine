"""
Capturas de la búsqueda de X con Playwright (Fase 3 del scraper local).

Abre Chromium con la sesión (storage_state) de una cuenta, navega a la búsqueda
del término (pestaña Recientes, lang:es), espera el feed y saca N capturas de
viewport scrolleando ~90% de pantalla entre una y otra, con esperas aleatorias
de ritmo humano.

No toca el pool de cuentas: si la sesión está muerta o X interpone un challenge,
lanza SesionInvalidaError y run.py decide la rotación.

Dry-run sin login ni X (prueba la maquinaria de scroll+captura contra cualquier
URL, ej. el feed falso de testdata servido por localhost):
    python browser.py --url http://127.0.0.1:8123/feed_falso.html --scrolls 3

Los selectores/tiempos REALES de X se tunean en la PC de la oficina. Playwright
se importa DIFERIDO: la suite corre sin playwright instalado.
"""
import argparse
import random
import sys
import time
import urllib.parse
from pathlib import Path

X_SEARCH_URL = "https://x.com/search?q={q}&src=typed_query&f=live"
# El feed de X renderiza cada post como <article>. Si X cambia, tunear acá.
SELECTOR_FEED = "article"
# URLs a las que X redirige cuando la sesión no sirve o hay challenge.
MARCAS_SESION_MUERTA = ("/login", "/account/access", "/i/flow")
TIMEOUT_FEED_MS = 30000


class SesionInvalidaError(Exception):
    """La sesión no sirve: redirect a login/challenge o el feed no apareció."""


def url_busqueda(termino: str) -> str:
    q = urllib.parse.quote(f"{termino} lang:es")
    return X_SEARCH_URL.format(q=q)


def esperar_aleatorio(rango) -> None:
    """Espera un tiempo aleatorio dentro del rango (ritmo humano)."""
    time.sleep(random.uniform(rango[0], rango[1]))


def capturar_pagina(page, scrolls: int, esperas, carpeta: Path, prefijo: str) -> list[Path]:
    """Captura el viewport `scrolls` veces scrolleando ~90% de pantalla entre capturas.
    Separado de la navegación para poder probarlo contra cualquier página."""
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []
    for n in range(scrolls):
        ruta = carpeta / f"{prefijo}-{n + 1}.png"
        page.screenshot(path=str(ruta))
        rutas.append(ruta)
        if n + 1 < scrolls:
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
            esperar_aleatorio(esperas)
    return rutas


def capturar_busqueda(sesion: Path, termino: str, scrolls: int = 3,
                      viewport=(950, 1300), headless: bool = True,
                      esperas=(2, 5), carpeta: Path = Path("capturas"),
                      prefijo: str = "captura") -> list[Path]:
    """Navega la búsqueda de X con la sesión dada y devuelve las rutas de los PNG.
    Lanza SesionInvalidaError si la sesión no sirve o el feed no aparece."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # diferido
    with sync_playwright() as p:
        # Mismo flag que el login (accounts.py): con navigator.webdriver=true X
        # interpone challenges, que acá se leerían como cuenta quemada.
        navegador = p.chromium.launch(
            headless=headless, args=["--disable-blink-features=AutomationControlled"])
        context = navegador.new_context(
            storage_state=str(sesion),
            viewport={"width": viewport[0], "height": viewport[1]})
        page = context.new_page()
        try:
            page.goto(url_busqueda(termino), timeout=TIMEOUT_FEED_MS)
            if any(marca in page.url for marca in MARCAS_SESION_MUERTA):
                raise SesionInvalidaError(f"redirigido a {page.url}")
            try:
                page.wait_for_selector(SELECTOR_FEED, timeout=TIMEOUT_FEED_MS)
            except PWTimeout:
                raise SesionInvalidaError("el feed no apareció (¿challenge o sesión vencida?)")
            esperar_aleatorio(esperas)  # dejar asentar el feed antes de la primera captura
            return capturar_pagina(page, scrolls, esperas, carpeta, prefijo)
        finally:
            navegador.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Dry-run de la maquinaria de capturas (sin login, contra cualquier URL).")
    ap.add_argument("--url", required=True, help="URL a capturar (ej. el feed falso en localhost)")
    ap.add_argument("--scrolls", type=int, default=3)
    ap.add_argument("--visible", action="store_true", help="navegador visible (default headless)")
    ap.add_argument("--out", default="capturas/dry-run", help="carpeta de salida")
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright  # diferido
    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=not args.visible)
        page = navegador.new_page(viewport={"width": 950, "height": 1300})
        page.goto(args.url)
        rutas = capturar_pagina(page, args.scrolls, (0.5, 1.0), Path(args.out), "dry")
        navegador.close()
    for r in rutas:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
