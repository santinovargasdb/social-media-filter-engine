"""
Caja de herramientas común de captura (compartida por redes/*): scroll+captura
de viewport o de un elemento, esperas de ritmo humano, sesión con storage_state
y el error de sesión inválida. Lo específico de cada red vive en redes/<red>.py.

Dry-run sin login ni red (prueba la maquinaria de scroll+captura contra cualquier
URL, ej. el feed falso de testdata servido por localhost):
    python browser.py --url http://127.0.0.1:8123/feed_falso.html --scrolls 3

Playwright se importa DIFERIDO: la suite corre sin playwright instalado.
"""
import argparse
import random
import sys
import time
from contextlib import contextmanager
from pathlib import Path

TIMEOUT_FEED_MS = 30000


class SesionInvalidaError(Exception):
    """La sesión no sirve: redirect a login/challenge o el feed no apareció."""


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


def capturar_elemento(page, selector: str, scrolls: int, esperas,
                      carpeta: Path, prefijo: str) -> list[Path]:
    """Como capturar_pagina, pero screenshotea y scrollea UN elemento (ej. el panel
    de comentarios de un video), no la ventana."""
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []
    for n in range(scrolls):
        ruta = carpeta / f"{prefijo}-{n + 1}.png"
        page.locator(selector).first.screenshot(path=str(ruta))
        rutas.append(ruta)
        if n + 1 < scrolls:
            page.eval_on_selector(selector, "el => el.scrollBy(0, el.clientHeight * 0.9)")
            esperar_aleatorio(esperas)
    return rutas


@contextmanager
def pagina_con_sesion(sesion: Path, viewport, headless: bool):
    """Abre Chromium con la sesión (storage_state) de una cuenta y entrega un Page.
    Cierra el navegador al salir. Mismo flag anti-detección que el login: con
    navigator.webdriver=true las redes interponen challenges que acá se leerían
    como cuenta quemada."""
    from playwright.sync_api import sync_playwright  # diferido
    with sync_playwright() as p:
        navegador = p.chromium.launch(
            headless=headless, args=["--disable-blink-features=AutomationControlled"])
        context = navegador.new_context(
            storage_state=str(sesion),
            viewport={"width": viewport[0], "height": viewport[1]})
        try:
            yield context.new_page()
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
