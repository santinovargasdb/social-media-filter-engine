# scraper_local/redes/instagram.py
"""Captura de posts de Instagram por PERFIL de cada candidato.

IG rompió el browse de hashtags en la web (verificado en vivo 2026-10-19:
/explore/tags/<x>/ redirige a /explore/search/keyword/?q=%23<x> y la página
devuelve "Se produjo un error y no se pudo cargar la página", con 0 posts —
pasa igual headless y visible, con sesión logueada y válida). Los PERFILES sí
cargan normal, así que se scrapean las cuentas oficiales de los candidatos:
se abre /<handle>/, se extraen los links de posts de la grilla (a[href*='/p/'])
y se capturan los primeros N.

Los selectores REALES se tunean en la PC de la oficina (marcados TUNEAR)."""
from pathlib import Path

import browser

SELECTOR_POST_LINKS = "a[href*='/p/']"   # links a posts en la grilla del perfil
MARCAS_SESION_MUERTA = ("/accounts/login/", "/challenge/")

# Candidato -> handle de IG (sin @). Sobreescribible/extensible en config.json
# → instagram.perfiles_candidatos: {"Nombre": "handle_sin_arroba"}.
# Handles verificados en vivo 2026-10-19 (cargan grilla con posts). CFK no tiene
# cuenta web cargable con los handles probados: se deja fuera y se configura
# aparte cuando se consiga el correcto (si no, queda un warning claro).
PERFILES_CANDIDATOS: dict[str, str] = {
    "Javier Milei":      "javiermilei",
    "Axel Kicillof":     "kicillofok",
    "Sergio Massa":      "sergiomassa",
    "Patricia Bullrich": "patobullrich",
}


def login_completado(url: str) -> bool:
    """True solo cuando el home feed está cargado. Excluye cualquier path
    /accounts/ (login, onetap, email-confirmation, etc.) y /challenge/."""
    return (
        "instagram.com" in url
        and "/accounts/" not in url
        and "/challenge/" not in url
    )


def links_de_posts(hrefs: list[str], cantidad: int) -> list[str]:
    """Primeros `cantidad` links de post únicos (contienen /p/), en orden de aparición."""
    out: list[str] = []
    for h in hrefs:
        if "/p/" in h and h not in out:
            out.append(h)
        if len(out) == cantidad:
            break
    return out


def _perfil_para(termino: str, cfg_red: dict) -> str | None:
    """Handle de IG (sin @) del candidato, o None si no hay uno configurado.
    El default se puede pisar/extender con cfg_red['perfiles_candidatos']."""
    mapping = dict(PERFILES_CANDIDATOS)
    mapping.update(cfg_red.get("perfiles_candidatos") or {})
    handle = mapping.get(termino)
    if not handle:
        return None
    return handle.strip().lstrip("@").strip()


def _verificar_sesion(page) -> None:
    if any(marca in page.url for marca in MARCAS_SESION_MUERTA):
        raise browser.SesionInvalidaError(f"redirigido a {page.url}")


def _perfil_no_disponible(page) -> bool:
    """IG muestra 'no está disponible' (handle inexistente) o 'se produjo un error'
    SIN cambiar la URL a login/challenge; por eso hay que leer el texto del body."""
    txt = page.evaluate("() => document.body.innerText.toLowerCase()")
    return ("no está disponible" in txt or "no disponible" in txt
            or "se produjo un error" in txt)


def capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict,
             carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]:
    from playwright.sync_api import TimeoutError as PWTimeout  # diferido
    esperas = tuple(cfg["espera_entre_scrolls"])

    # En IG solo abrimos posts de los punteros (candidatos_posts); el resto no se captura.
    if termino not in (cfg_red.get("candidatos_posts") or []):
        return []

    handle = _perfil_para(termino, cfg_red)
    if not handle:
        warnings.append(
            f"[instagram] Sin perfil de IG configurado para '{termino}' "
            "(agregá su handle en instagram.perfiles_candidatos).")
        return []

    with browser.pagina_con_sesion(sesion, tuple(cfg["viewport"]), cfg["headless"]) as page:
        page.goto(f"https://www.instagram.com/{handle}/", timeout=browser.TIMEOUT_FEED_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        browser.esperar_aleatorio((2, 4))
        _verificar_sesion(page)
        if _perfil_no_disponible(page):
            warnings.append(f"[instagram] El perfil @{handle} de '{termino}' no cargó "
                            "(¿handle incorrecto o cuenta privada?).")
            return []
        try:
            page.wait_for_selector(SELECTOR_POST_LINKS, timeout=browser.TIMEOUT_FEED_MS)
        except PWTimeout:
            warnings.append(f"[instagram] Sin posts visibles en @{handle} ('{termino}').")
            return []

        # Scroll para exponer más posts antes de extraer los links.
        for _ in range(cfg_red.get("scrolls_por_candidato", 1)):
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
            browser.esperar_aleatorio(esperas)

        hrefs = page.eval_on_selector_all(SELECTOR_POST_LINKS, "els => els.map(e => e.href)")
        post_links = links_de_posts(hrefs, cfg_red.get("posts_por_candidato", 3))
        capturas = []
        for i, link in enumerate(post_links):
            try:
                page.goto(link, timeout=browser.TIMEOUT_FEED_MS)
                _verificar_sesion(page)
                browser.esperar_aleatorio((2, 3))
                contexto = (
                    f"publicación de Instagram del perfil de {termino}; "
                    "la caption y los comentarios visibles cuentan como publicaciones separadas"
                )
                rutas = browser.capturar_pagina(
                    page, 1, esperas, carpeta, f"{prefijo}-post-{i + 1}")
                capturas.extend({"ruta": r, "contexto": contexto} for r in rutas)
            except browser.SesionInvalidaError:
                raise
            except Exception as e:
                warnings.append(
                    f"[instagram] Post {i + 1} de '{termino}' sin capturar ({e}).")
    return capturas
