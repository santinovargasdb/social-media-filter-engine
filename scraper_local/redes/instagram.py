# scraper_local/redes/instagram.py
"""Búsqueda en Instagram vía sidebar search + posts individuales para punteros.

Flujo: Home → click icono búsqueda sidebar → tipeo con delay humano →
pestaña Posts → extrae links de posts vía DOM (sin Gemini sobre la grilla,
los thumbnails no tienen texto) → abre posts de punteros y los captura.

Los selectores REALES se tunean en la PC de la oficina (marcados TUNEAR)."""
import unicodedata
from pathlib import Path

import browser

IG_HOME_URL = "https://www.instagram.com/"
LOGIN_URL   = "https://www.instagram.com/accounts/login/"

SELECTOR_POST_LINKS = "a[href*='/p/']"   # links a posts en grilla de hashtag # TUNEAR
SELECTOR_CHALLENGE  = "[data-testid*='challenge'], [class*='challenge']"
MARCAS_SESION_MUERTA = ("/accounts/login/", "/challenge/")

# Hashtags por defecto para cada candidato. Sobreescribibles en config.json
# → instagram.hashtags_candidatos: {"Nombre": "hashtag_sin_numeral"}
HASHTAGS_CANDIDATOS: dict[str, str] = {
    "Javier Milei":                   "javierMilei",
    "Axel Kicillof":                  "kicillof",
    "Sergio Massa":                   "sergiomassa",
    "Patricia Bullrich":              "bullrich",
    "Cristina Fernández de Kirchner": "cfk",
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


def _plegar_ascii(texto: str) -> str:
    """Quita marcas diacríticas (ñ→n, tildes fuera) vía descomposición NFD."""
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _hashtags_para(termino: str, cfg_red: dict) -> list[str]:
    """Hashtags de Instagram a intentar para el término dado (sin #), en orden.

    El primario sale del mapping (defaults sobreescritos por cfg_red) o del
    término sin espacios ni símbolos; si tiene diacríticos se agrega la
    variante plegada a ASCII como fallback (SergioUñac → SergioUnac)."""
    mapping = dict(HASHTAGS_CANDIDATOS)
    mapping.update(cfg_red.get("hashtags_candidatos") or {})
    if termino in mapping:
        primario = mapping[termino]
    else:
        primario = "".join(c for c in termino.replace(" ", "") if c.isalnum())
    plegado = _plegar_ascii(primario)
    return [primario] if plegado == primario else [primario, plegado]


def _verificar_sesion(page) -> None:
    if any(marca in page.url for marca in MARCAS_SESION_MUERTA):
        raise browser.SesionInvalidaError(f"redirigido a {page.url}")


def capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict,
             carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]:
    from playwright.sync_api import TimeoutError as PWTimeout  # diferido
    esperas = tuple(cfg["espera_entre_scrolls"])
    hashtags = _hashtags_para(termino, cfg_red)

    with browser.pagina_con_sesion(sesion, tuple(cfg["viewport"]), cfg["headless"]) as page:
        # Probar cada variante de hashtag hasta que una muestre posts
        for hashtag in hashtags:
            page.goto(f"https://www.instagram.com/explore/tags/{hashtag}/",
                      timeout=browser.TIMEOUT_FEED_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            browser.esperar_aleatorio((2, 4))
            _verificar_sesion(page)
            try:
                page.wait_for_selector(SELECTOR_POST_LINKS, timeout=browser.TIMEOUT_FEED_MS)
                break
            except PWTimeout:
                continue
        else:
            intentados = " ni ".join(f"'#{h}'" for h in hashtags)
            warnings.append(
                f"[instagram] Sin posts para {intentados} (¿hashtag inexistente?).")
            return []

        # Scroll para exponer más posts antes de extraer
        for _ in range(cfg_red.get("scrolls_por_candidato", 1)):
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
            browser.esperar_aleatorio(esperas)

        hrefs = page.eval_on_selector_all(SELECTOR_POST_LINKS, "els => els.map(e => e.href)")

        candidatos_posts = cfg_red.get("candidatos_posts") or []
        if termino not in candidatos_posts:
            return []  # candidato no puntero: sin Gemini para IG

        posts_por_candidato = cfg_red.get("posts_por_candidato", 3)
        post_links = links_de_posts(hrefs, posts_por_candidato)
        capturas = []
        for i, link in enumerate(post_links):
            try:
                page.goto(link, timeout=browser.TIMEOUT_FEED_MS)
                _verificar_sesion(page)
                browser.esperar_aleatorio((2, 3))
                contexto = (
                    f"publicación de Instagram sobre {termino}; "
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
