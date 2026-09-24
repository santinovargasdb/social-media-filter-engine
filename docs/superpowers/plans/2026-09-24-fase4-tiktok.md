# Fase 4 — TikTok multi-red: Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extender el scraper local de la Boca de Urna (hoy solo X) a TikTok: búsqueda por candidato + comentarios de los videos top de los punteros, fusionado en el mismo snapshot con `por_red`.

**Architecture:** Módulo por red con contrato común (`scraper_local/redes/`): `browser.py` queda como caja de herramientas, `accounts.py` maneja pools por red, `run.py` itera las redes activas y arma un bloque por red que `electoral._merge_bloques` ya sabe fusionar. Cero cambios en backend/frontend.

**Tech Stack:** Python 3.11 (venv en `scraper_local/.venv`), Playwright (import diferido), Gemini visión vía `backend/gemini_client.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-fase4-tiktok-design.md`

## Global Constraints

- Todo el código, docstrings y comentarios en **español**, mismo estilo que los módulos existentes.
- Playwright se importa **DIFERIDO** (adentro de la función que lo usa): la suite corre sin Playwright instalado.
- Tests **sin red, sin browser, sin API key** — todo mockeado. Comando: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local` desde la raíz del repo. La suite completa (backend + scraper) debe quedar verde: `backend\.venv\Scripts\python.exe -m pytest -q`.
- Los selectores reales de TikTok se marcan `# TUNEAR` (se ajustan en vivo en la PC de la oficina, no en CI).
- Las sesiones ya logueadas de X (`accounts.json`, `.sesiones/cuenta1.json`, `.sesiones/cuenta2.json`) **deben seguir valiendo sin re-login**.
- Cuota: TikTok solo agrega capturas para `candidatos_comentarios` (5 punteros × 2 videos × 2 capturas); no tocar `VISION_MIN_INTERVAL`.
- Working dir de los comandos: raíz del repo (`C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT`), salvo indicación.

---

### Task 1: `vision.py` — parámetro `contexto` en el prompt

**Files:**
- Modify: `scraper_local/vision.py`
- Test: `scraper_local/test_vision.py`

**Interfaces:**
- Consumes: nada nuevo.
- Produces: `read_capture(image, red, candidatos=None, mime=None, contexto="") -> list[dict] | None` y `_build_vision_prompt(red, candidatos, contexto="") -> str`. Task 7 (run.py) llama `read_capture(..., contexto=cap["contexto"])`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `scraper_local/test_vision.py`:

```python
def test_build_prompt_incluye_contexto():
    prompt = vision._build_vision_prompt("tiktok", ["Javier Milei"],
                                         contexto="panel de comentarios de un video")
    assert "CONTEXTO DE LA CAPTURA: panel de comentarios de un video" in prompt
    assert '"tiktok"' in prompt


def test_build_prompt_sin_contexto_no_agrega_seccion():
    prompt = vision._build_vision_prompt("twitter", ["Javier Milei"])
    assert "CONTEXTO DE LA CAPTURA" not in prompt


def test_read_capture_pasa_contexto_al_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    visto = _mock_gemini(monkeypatch)
    vision.read_capture(cap, "tiktok", contexto="comentarios sobre Milei")
    assert "CONTEXTO DE LA CAPTURA: comentarios sobre Milei" in visto["prompt"]
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local\test_vision.py -k contexto`
Expected: FAIL — `TypeError: _build_vision_prompt() got an unexpected keyword argument 'contexto'`

- [ ] **Step 3: Implementación mínima**

En `scraper_local/vision.py`, cambiar la firma de `_build_vision_prompt` y el arranque del prompt:

```python
def _build_vision_prompt(red: str, candidatos: list[str], contexto: str = "") -> str:
    lista = ", ".join(candidatos)
    ctx = f"\nCONTEXTO DE LA CAPTURA: {contexto}.\n" if contexto else ""
    return f"""Sos un analista de opinión pública que evalúas publicaciones de redes sociales del ámbito argentino de cara a las próximas elecciones presidenciales.

Vas a recibir UNA CAPTURA DE PANTALLA de la red social "{red}". Tu tarea es EXTRAER cada publicación visible y clasificarla, en una sola pasada.
{ctx}
REGLAS DE LECTURA DE PANTALLA (OBLIGATORIAS):"""
```

(el resto del prompt queda idéntico — solo se inserta `{ctx}` entre el párrafo de la tarea y "REGLAS DE LECTURA").

Y en `read_capture`:

```python
def read_capture(image, red: str, candidatos: list[str] | None = None,
                 mime: str | None = None, contexto: str = "") -> list[dict] | None:
    """Lee UNA captura con Gemini visión. `contexto` describe la captura cuando no es
    un feed común (ej. panel de comentarios). Devuelve posts saneados, [] si no se
    vio ninguno, o None si el transporte falló (mismo contrato que electoral)."""
    data_b64, mime = _load_image(image, mime)
    prompt = _build_vision_prompt(red, candidatos or CANDIDATOS_DEFAULT, contexto)
    _pace()
    parsed, _status = gemini_client.run_with_rotation(prompt, image=(data_b64, mime))
    if parsed is None:
        print("ERROR vision: Gemini no devolvió resultado (upstream).")
        return None
    return _sanitize_posts(parsed, red)
```

- [ ] **Step 4: Correr la suite del scraper**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: PASS (42 tests: 39 previos + 3 nuevos)

- [ ] **Step 5: Commit**

```bash
git add scraper_local/vision.py scraper_local/test_vision.py
git commit -m "feat(scraper): vision.py acepta contexto de captura (comentarios TikTok)"
```

---

### Task 2: `dedup.py` — la red integra la clave

**Files:**
- Modify: `scraper_local/dedup.py`
- Test: `scraper_local/test_dedup.py`

**Interfaces:**
- Consumes: posts con campo `"red"` (ya lo traen: `vision._sanitize_posts` lo setea).
- Produces: `dedup_posts(posts)` sin cambio de firma; clave interna `(red, autor, hash_texto)`.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `scraper_local/test_dedup.py`:

```python
def test_mismo_autor_y_texto_en_redes_distintas_no_colapsa():
    a = {"autor": "@user", "texto": "Vamos Milei", "red": "twitter"}
    b = {"autor": "@user", "texto": "Vamos Milei", "red": "tiktok"}
    assert len(dedup_posts([a, b])) == 2


def test_duplicado_en_la_misma_red_si_colapsa():
    a = {"autor": "@user", "texto": "Vamos Milei", "red": "tiktok"}
    assert dedup_posts([a, dict(a)]) == [a]
```

(usar el mismo import que ya tenga el archivo — `from dedup import dedup_posts` o `import dedup` según el estilo existente; ajustar las llamadas en consecuencia.)

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local\test_dedup.py -k redes_distintas`
Expected: FAIL — devuelve 1 post en vez de 2.

- [ ] **Step 3: Implementación mínima**

En `scraper_local/dedup.py`, reemplazar `_clave`:

```python
def _clave(post: dict, indice: int) -> tuple:
    red = (post.get("red") or "").strip().lower()
    autor = (post.get("autor") or "").strip().lower().lstrip("@")
    texto = " ".join((post.get("texto") or "").lower().split())[:_TEXTO_CHARS]
    if not autor and not texto:
        # Sin autor ni texto no hay identidad: que no colapsen entre sí.
        return ("", "", indice)
    return (red, autor, hashlib.md5(texto.encode("utf-8")).hexdigest()[:16])
```

Actualizar la primera línea del docstring del módulo: la clave es `(red, autor normalizado, hash del texto normalizado)`.

- [ ] **Step 4: Correr la suite del scraper**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scraper_local/dedup.py scraper_local/test_dedup.py
git commit -m "feat(scraper): dedup por red — mismo texto en redes distintas no colapsa"
```

---

### Task 3: `browser.py` — `pagina_con_sesion` y `capturar_elemento`

**Files:**
- Modify: `scraper_local/browser.py`
- Test: `scraper_local/test_browser.py`

**Interfaces:**
- Consumes: nada nuevo.
- Produces (las usan Tasks 4 y 6):
  - `pagina_con_sesion(sesion: Path, viewport: tuple, headless: bool)` — context manager que entrega un `Page` de Playwright con la sesión cargada y cierra el navegador al salir.
  - `capturar_elemento(page, selector: str, scrolls: int, esperas, carpeta: Path, prefijo: str) -> list[Path]` — screenshotea y scrollea UN elemento (no la ventana).

En esta task **no se borra nada** de browser.py (lo X-specific se muda en la Task 4).

- [ ] **Step 1: Escribir los tests que fallan**

Agregar a `scraper_local/test_browser.py`. `FakePage` se extiende para soportar elementos:

```python
class FakeLocator:
    def __init__(self, page):
        self.page = page
        self.first = self

    def screenshot(self, path):
        Path(path).write_bytes(b"png-elemento")


class FakePageConElemento(FakePage):
    def __init__(self):
        super().__init__()
        self.selectores_evaluados = []

    def locator(self, selector):
        return FakeLocator(self)

    def eval_on_selector(self, selector, script):
        self.selectores_evaluados.append((selector, script))


def test_capturar_elemento_screenshotea_y_scrollea_el_elemento(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePageConElemento()
    rutas = browser.capturar_elemento(page, "#comentarios", scrolls=3, esperas=(0, 0),
                                      carpeta=tmp_path / "caps", prefijo="milei-comentarios-1")
    assert [r.name for r in rutas] == ["milei-comentarios-1-1.png",
                                       "milei-comentarios-1-2.png",
                                       "milei-comentarios-1-3.png"]
    assert all(r.read_bytes() == b"png-elemento" for r in rutas)
    # Scrollea el ELEMENTO entre capturas: n-1 scrolls.
    assert len(page.selectores_evaluados) == 2
    sel, script = page.selectores_evaluados[0]
    assert sel == "#comentarios" and "scrollBy" in script and "clientHeight" in script


def test_capturar_elemento_un_scroll_no_scrollea(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePageConElemento()
    rutas = browser.capturar_elemento(page, "#c", scrolls=1, esperas=(0, 0),
                                      carpeta=tmp_path, prefijo="uno")
    assert len(rutas) == 1 and page.selectores_evaluados == []
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local\test_browser.py -k elemento`
Expected: FAIL — `AttributeError: module 'browser' has no attribute 'capturar_elemento'`

- [ ] **Step 3: Implementación mínima**

En `scraper_local/browser.py`, agregar `from contextlib import contextmanager` a los imports y estas dos funciones (después de `capturar_pagina`):

```python
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
```

- [ ] **Step 4: Correr la suite del scraper**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scraper_local/browser.py scraper_local/test_browser.py
git commit -m "feat(scraper): browser.py gana pagina_con_sesion y capturar_elemento"
```

---

### Task 4: paquete `redes/` + `redes/twitter.py` (mover la búsqueda de X)

**Files:**
- Create: `scraper_local/redes/__init__.py`
- Create: `scraper_local/redes/twitter.py`
- Create: `scraper_local/test_redes_twitter.py`
- Modify: `scraper_local/browser.py` (borrar lo X-specific ya mudado)
- Modify: `scraper_local/run.py` (call sites; sigue single-red por ahora)
- Modify: `scraper_local/test_browser.py` (mover el test de `url_busqueda`)
- Modify: `scraper_local/test_run.py` (mocks apuntan al módulo nuevo)

**Interfaces:**
- Consumes: `browser.pagina_con_sesion`, `browser.capturar_pagina`, `browser.esperar_aleatorio`, `browser.SesionInvalidaError`, `browser.TIMEOUT_FEED_MS` (Task 3).
- Produces — el CONTRATO DE RED que Task 6 (tiktok) replica y Tasks 5/7 consumen:
  - `capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict, carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]` — cada dict es `{"ruta": Path, "contexto": str}`; lanza `browser.SesionInvalidaError`.
  - `LOGIN_URL: str` y `login_completado(url: str) -> bool` (los usa accounts en Task 5).
  - `redes.POR_NOMBRE: dict[str, module]` — registro de redes.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `scraper_local/test_redes_twitter.py`:

```python
"""Tests de redes/twitter.py — partes puras (la navegación real se verifica en vivo)."""
import redes
from redes import twitter


def test_registro_incluye_twitter():
    assert redes.POR_NOMBRE["twitter"] is twitter


def test_url_busqueda_encodea_termino_y_lang():
    url = twitter.url_busqueda("Javier Milei")
    assert url.startswith("https://x.com/search?q=")
    assert "Javier%20Milei%20lang%3Aes" in url
    assert "f=live" in url


def test_login_completado_solo_en_home():
    assert twitter.login_completado("https://x.com/home") is True
    assert twitter.login_completado("https://x.com/login") is False
    assert twitter.login_completado("https://x.com/i/flow/login") is False
```

En `scraper_local/test_browser.py`: **borrar** `test_url_busqueda_encodea_termino_y_lang` (se mudó arriba).

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local\test_redes_twitter.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'redes'`

- [ ] **Step 3: Crear el paquete y mover la lógica**

Crear `scraper_local/redes/__init__.py`:

```python
"""Registro de redes del scraper local. Cada módulo cumple el contrato:

    capturar(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings)
        -> list[{"ruta": Path, "contexto": str}]     (SesionInvalidaError si muere)
    LOGIN_URL / login_completado(url)                (los usa accounts.py)
"""
from . import twitter

POR_NOMBRE = {"twitter": twitter}
```

Crear `scraper_local/redes/twitter.py` (la lógica es la de `browser.capturar_busqueda`, adaptada al contrato):

```python
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
```

En `scraper_local/browser.py` **borrar**: `X_SEARCH_URL`, `SELECTOR_FEED`, `MARCAS_SESION_MUERTA`, `url_busqueda`, `capturar_busqueda` y el import de `urllib.parse`. Actualizar el docstring del módulo: "Caja de herramientas común de captura (compartida por redes/*): scroll+captura de viewport o de un elemento, esperas de ritmo humano, sesión con storage_state y el error de sesión inválida. Lo específico de cada red vive en redes/<red>.py." El CLI `main()` (dry-run contra una URL) queda como está.

En `scraper_local/run.py`:
- Import: agregar `import redes` (junto a los otros imports locales); `browser` sigue importado (usa `esperar_aleatorio` y `SesionInvalidaError`).
- `capturar_candidato`: reemplazar el cuerpo del `try` para llamar al módulo de red (sigue hardcodeado twitter en esta task) y renombrar la variable de retorno:

```python
        try:
            capturas = redes.POR_NOMBRE["twitter"].capturar(
                sesion=accounts.ruta_sesion(cuenta["alias"]), termino=candidato,
                cfg=cfg, cfg_red={"scrolls_por_candidato": cfg["scrolls_por_candidato"]},
                carpeta=carpeta, prefijo=_slug(candidato), warnings=warnings)
            accounts.registrar_uso(pool, cuenta["alias"])
            return capturas
        except browser.SesionInvalidaError as e:
```

- En `correr`, adaptar el loop de lectura al nuevo tipo de retorno:

```python
        capturas = capturar_candidato(cfg, pool, candidato, carpeta, warnings)
        for cap in capturas:
            leidos = vision.read_capture(cap["ruta"], red, candidatos, contexto=cap["contexto"])
            if leidos is None:
                errores += 1
                warnings.append(f"Lectura fallida (Gemini) de {cap['ruta'].name}.")
                continue
            posts_crudos.extend(leidos)
        if i + 1 < len(candidatos) and capturas:
```

En `scraper_local/test_run.py`:
- `test_capturar_candidato_rota_ante_sesion_invalida` y `test_capturar_candidato_error_inesperado_no_quema_ni_aborta`: el monkeypatch pasa de `run.browser.capturar_busqueda` a `redes.twitter.capturar` (agregar `from redes import twitter` al import block). El fake nuevo:

```python
    def fake_capturar(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings):
        usadas.append(sesion.name)
        if sesion.name == "muerta.json":
            raise browser_mod.SesionInvalidaError("challenge")
        return [{"ruta": tmp_path / "cap-1.png", "contexto": ""}]
    monkeypatch.setattr(twitter, "capturar", fake_capturar)
```

  (y el fake de `explota` cambia a la misma firma). La aserción `len(rutas) == 1` pasa a `len(capturas) == 1`.
- `_preparar_correr`: `run.capturar_candidato` se mockea devolviendo el nuevo shape y `read_capture` acepta `contexto`:

```python
    monkeypatch.setattr(run, "capturar_candidato",
                        lambda cfg, pool, cand, carpeta, warnings: [
                            {"ruta": tmp_path / f"{cand}.png", "contexto": ""}])
    monkeypatch.setattr(run.vision, "read_capture",
                        lambda ruta, red, candidatos=None, contexto="": posts_por_captura)
```

- `test_correr_pasa_candidatos_a_vision`: el lambda también suma `contexto=""` a la firma.

- [ ] **Step 4: Correr la suite completa**

Run: `backend\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS (backend intacto + scraper verde con los tests movidos)

- [ ] **Step 5: Commit**

```bash
git add scraper_local/redes/ scraper_local/browser.py scraper_local/run.py scraper_local/test_redes_twitter.py scraper_local/test_browser.py scraper_local/test_run.py
git commit -m "refactor(scraper): paquete redes/ con contrato comun; X se muda a redes/twitter.py"
```

---

### Task 5: `accounts.py` — pools y login por red

**Files:**
- Modify: `scraper_local/accounts.py`
- Modify: `scraper_local/test_accounts.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `redes.POR_NOMBRE` con `LOGIN_URL` / `login_completado(url)` (Task 4).
- Produces (los usa Task 7):
  - `ruta_pool(red: str = "twitter") -> Path` — twitter → `accounts.json` (legacy intacto); otra red → `accounts-<red>.json`.
  - `ruta_sesion(alias: str, red: str = "twitter") -> Path` — twitter → `.sesiones/<alias>.json` (legacy intacto); otra red → `.sesiones/<red>-<alias>.json`.
  - `cargar_pool(ruta=None, red="twitter")` / `guardar_pool(pool, ruta=None, red="twitter")` — `ruta` explícita gana; si no, `ruta_pool(red)`.
  - CLI: `python accounts.py login <alias> [--red tiktok]` y `python accounts.py estado [--red tiktok]`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `scraper_local/test_accounts.py`:

```python
def test_ruta_pool_twitter_conserva_legacy():
    assert accounts.ruta_pool("twitter").name == "accounts.json"
    assert accounts.ruta_pool("tiktok").name == "accounts-tiktok.json"


def test_ruta_sesion_twitter_conserva_legacy():
    assert accounts.ruta_sesion("cuenta1").name == "cuenta1.json"
    assert accounts.ruta_sesion("cuenta1", "twitter").name == "cuenta1.json"
    assert accounts.ruta_sesion("tt1", "tiktok").name == "tiktok-tt1.json"


def test_cargar_pool_por_red_usa_su_archivo(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "BASE_DIR", tmp_path)
    accounts.guardar_pool(_pool(A1), red="tiktok")
    assert (tmp_path / "accounts-tiktok.json").exists()
    assert not (tmp_path / "accounts.json").exists()
    assert accounts.cargar_pool(red="tiktok") == _pool(A1)
    assert accounts.cargar_pool(red="twitter") == {"cuentas": []}
```

Nota: `ruta_pool`/`ruta_sesion` deben calcular sobre `BASE_DIR`/`DIR_SESIONES` en el momento de la llamada (no constantes precalculadas a nivel módulo) para que el monkeypatch de `BASE_DIR` funcione — ver Step 3.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local\test_accounts.py -k "ruta_pool or por_red or ruta_sesion"`
Expected: FAIL — `AttributeError: module 'accounts' has no attribute 'ruta_pool'`

- [ ] **Step 3: Implementación**

En `scraper_local/accounts.py`:

1. Borrar la constante `RUTA_POOL` y las constantes `X_LOGIN_URL` / `X_HOME_GLOB` (la URL y la detección ahora vienen del módulo de red). Agregar `import redes` después de los imports stdlib. Docstring del módulo: actualizar a "Cuentas descartables POR RED para el scraper local (`--red`, default twitter)… Pool en accounts.json (twitter, legacy) / accounts-<red>.json".

2. Helpers de rutas y firmas nuevas:

```python
def ruta_pool(red: str = "twitter") -> Path:
    """twitter conserva accounts.json (legacy, pre multi-red); el resto va por red."""
    nombre = "accounts.json" if red == "twitter" else f"accounts-{red}.json"
    return BASE_DIR / nombre


def cargar_pool(ruta=None, red: str = "twitter") -> dict:
    ruta = Path(ruta) if ruta else ruta_pool(red)
    if not ruta.exists():
        return {"cuentas": []}
    return json.loads(ruta.read_text(encoding="utf-8"))


def guardar_pool(pool: dict, ruta=None, red: str = "twitter") -> None:
    ruta = Path(ruta) if ruta else ruta_pool(red)
    ruta.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def ruta_sesion(alias: str, red: str = "twitter") -> Path:
    """twitter conserva <alias>.json (las sesiones ya logueadas siguen valiendo)."""
    nombre = f"{alias}.json" if red == "twitter" else f"{red}-{alias}.json"
    return BASE_DIR / ".sesiones" / nombre
```

   (borrar también la constante `DIR_SESIONES` y usar `BASE_DIR / ".sesiones"` en `_login` para el mkdir — así el monkeypatch de `BASE_DIR` alcanza para todo.)

3. `_login` y `_estado` parametrizados por red:

```python
def _login(alias: str, red: str) -> int:
    """Login manual: navegador visible; se guarda solo al detectar que el login terminó
    (la detección es por red: X redirige a /home, TikTok sale de /login)."""
    from playwright.sync_api import sync_playwright  # diferido: solo el CLI lo necesita
    red_mod = redes.POR_NOMBRE[red]
    (BASE_DIR / ".sesiones").mkdir(exist_ok=True)
    pool = cargar_pool(red=red)
    if not any(c.get("alias") == alias for c in pool.get("cuentas", [])):
        pool.setdefault("cuentas", []).append(
            {"alias": alias, "estado": "activa", "ultima_vez": "", "notas": ""})
    with sync_playwright() as p:
        # (mismo comentario y flags de ventana/anti-detección que hoy — no tocar)
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized", "--force-device-scale-factor=1",
                  "--disable-blink-features=AutomationControlled"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(red_mod.LOGIN_URL)
        print(f"Logueá la cuenta '{alias}' de {red} en la ventana del navegador.")
        print("(Usá el login nativo con mail+contraseña, NO 'Continuar con Google'. "
              "Si algo queda recortado: scrolleá dentro del modal o achicá con Ctrl+menos.)")
        print("Cuando estés adentro se guarda solo (detecta que saliste del login).")
        try:
            page.wait_for_url(red_mod.login_completado, timeout=LOGIN_TIMEOUT_MS)
        except Exception:
            print(f"ERROR: no se detectó el login en {LOGIN_TIMEOUT_MS // 60000} min "
                  "(o se cerró la ventana). La cuenta NO se agregó; reintentá.")
            try:
                browser.close()
            except Exception:
                pass
            return 1
        context.storage_state(path=str(ruta_sesion(alias, red)))
        browser.close()
    for c in pool["cuentas"]:
        if c["alias"] == alias:
            c["estado"] = "activa"
    guardar_pool(pool, red=red)
    print(f"Sesión guardada en {ruta_sesion(alias, red)}. Cuenta '{alias}' activa.")
    return 0


def _estado(red: str) -> int:
    pool = cargar_pool(red=red)
    if not pool.get("cuentas"):
        print(f"Pool de {red} vacío. Agregá cuentas con: python accounts.py login <alias> --red {red}")
        return 0
    for c in pool["cuentas"]:
        sesion = "sesión OK" if ruta_sesion(c["alias"], red).exists() else "SIN sesión"
        print(f"- {c['alias']}: {c['estado']} · {sesion} · última vez: {c.get('ultima_vez') or 'nunca'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cuentas descartables del scraper (login manual, por red).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    login = sub.add_parser("login", help="login manual de una cuenta (navegador visible)")
    login.add_argument("alias")
    login.add_argument("--red", default="twitter", choices=sorted(redes.POR_NOMBRE))
    estado = sub.add_parser("estado", help="lista el pool")
    estado.add_argument("--red", default="twitter", choices=sorted(redes.POR_NOMBRE))
    args = ap.parse_args(argv)
    if args.cmd == "login":
        return _login(args.alias, args.red)
    return _estado(args.red)
```

4. En `.gitignore`, reemplazar la línea `scraper_local/accounts.json` por:

```
scraper_local/accounts.json
scraper_local/accounts-*.json
```

- [ ] **Step 4: Correr la suite + smoke del CLI**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: PASS

Run (desde `scraper_local/`, verifica que el pool legacy de X sigue intacto): `.venv\Scripts\python.exe accounts.py estado`
Expected: `- cuenta1: activa · sesión OK · …` y `- cuenta2: activa · sesión OK · …`

- [ ] **Step 5: Commit**

```bash
git add scraper_local/accounts.py scraper_local/test_accounts.py .gitignore
git commit -m "feat(scraper): pools de cuentas y login por red (twitter legacy intacto)"
```

---

### Task 6: `redes/tiktok.py` — búsqueda + comentarios top

**Files:**
- Create: `scraper_local/redes/tiktok.py`
- Create: `scraper_local/test_redes_tiktok.py`
- Modify: `scraper_local/redes/__init__.py` (registrar tiktok)

**Interfaces:**
- Consumes: `browser.pagina_con_sesion`, `browser.capturar_pagina`, `browser.capturar_elemento`, `browser.esperar_aleatorio`, `browser.SesionInvalidaError`, `browser.TIMEOUT_FEED_MS` (Task 3) y el contrato de red (Task 4).
- Produces: `redes.POR_NOMBRE["tiktok"]` con el contrato completo (`capturar`, `LOGIN_URL`, `login_completado`) + `url_busqueda(termino)` y `links_de_videos(hrefs, cantidad)` (puras, testeables). `cfg_red` de tiktok usa las claves: `scrolls_por_candidato`, `videos_comentarios`, `scrolls_comentarios`, `candidatos_comentarios`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `scraper_local/test_redes_tiktok.py`:

```python
"""Tests de redes/tiktok.py — partes puras (los selectores reales se tunean en vivo)."""
import redes
from redes import tiktok


def test_registro_incluye_tiktok():
    assert redes.POR_NOMBRE["tiktok"] is tiktok


def test_url_busqueda_encodea_el_termino():
    url = tiktok.url_busqueda("Javier Milei")
    assert url == "https://www.tiktok.com/search?q=Javier%20Milei"


def test_login_completado_fuera_de_login_y_signup():
    assert tiktok.login_completado("https://www.tiktok.com/foryou") is True
    assert tiktok.login_completado("https://www.tiktok.com/") is True
    assert tiktok.login_completado("https://www.tiktok.com/login") is False
    assert tiktok.login_completado("https://www.tiktok.com/login/phone-or-email") is False
    assert tiktok.login_completado("https://www.tiktok.com/signup") is False


def test_links_de_videos_filtra_dedupea_y_corta():
    hrefs = [
        "https://www.tiktok.com/@a/video/111",
        "https://www.tiktok.com/@a",                  # perfil: afuera
        "https://www.tiktok.com/@a/video/111",        # repetido: afuera
        "https://www.tiktok.com/@b/video/222",
        "https://www.tiktok.com/@c/video/333",
    ]
    assert tiktok.links_de_videos(hrefs, 2) == [
        "https://www.tiktok.com/@a/video/111",
        "https://www.tiktok.com/@b/video/222",
    ]


def test_links_de_videos_menos_que_pedidos():
    assert tiktok.links_de_videos(["https://t/@a/video/1"], 5) == ["https://t/@a/video/1"]
    assert tiktok.links_de_videos([], 3) == []
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local\test_redes_tiktok.py`
Expected: FAIL — `ImportError: cannot import name 'tiktok' from 'redes'`

- [ ] **Step 3: Implementación**

Crear `scraper_local/redes/tiktok.py`:

```python
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
```

En `scraper_local/redes/__init__.py`:

```python
from . import tiktok, twitter

POR_NOMBRE = {"twitter": twitter, "tiktok": tiktok}
```

- [ ] **Step 4: Correr la suite del scraper**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scraper_local/redes/tiktok.py scraper_local/redes/__init__.py scraper_local/test_redes_tiktok.py
git commit -m "feat(scraper): redes/tiktok.py — busqueda + comentarios top con degradacion"
```

---

### Task 7: `run.py` multi-red + `config.json` nuevo formato

**Files:**
- Modify: `scraper_local/run.py`
- Modify: `scraper_local/config.json`
- Test: `scraper_local/test_run.py`

**Interfaces:**
- Consumes: `redes.POR_NOMBRE` (Tasks 4/6), `accounts.cargar_pool/guardar_pool/ruta_sesion` con `red=` (Task 5), `vision.read_capture(..., contexto=)` (Task 1).
- Produces:
  - `cargar_config` → cfg con `cfg["redes"]: dict[str, dict]` (traduce el formato legacy `"red"`/`"scrolls_por_candidato"` planos).
  - `filtrar_redes(cfg, redes_csv: str | None) -> dict` — aplica `--redes`; lanza `SystemExit` si queda vacío.
  - `capturar_candidato(red_nombre, cfg, pool, candidato, carpeta, warnings) -> list[dict]`.
  - `armar_bloque(posts, red, busquedas, crudos, errores)` — IDs `f"{red}_{i}"` (evita colisiones al fusionar bloques).
  - `armar_payload(bloques: list[dict], warnings) -> dict`.
  - CLI: `run.py [--dry-run] [--config ruta] [--redes twitter,tiktok]`.

- [ ] **Step 1: Escribir los tests que fallan**

En `scraper_local/test_run.py`:

1. Reemplazar `test_cargar_config_defaults_y_override` y agregar los nuevos:

```python
def test_cargar_config_defaults_y_override(tmp_path):
    assert run.cargar_config(tmp_path / "nada.json")["redes"] == {
        "twitter": {"scrolls_por_candidato": 3}}
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"redes": {"twitter": {"scrolls_por_candidato": 7}}}),
                    encoding="utf-8")
    cfg = run.cargar_config(ruta)
    assert cfg["redes"]["twitter"]["scrolls_por_candidato"] == 7
    assert cfg["headless"] is True  # el resto conserva el default


def test_cargar_config_traduce_formato_legacy(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"red": "twitter", "scrolls_por_candidato": 5,
                                "headless": False}), encoding="utf-8")
    cfg = run.cargar_config(ruta)
    assert cfg["redes"] == {"twitter": {"scrolls_por_candidato": 5}}
    assert "red" not in cfg and "scrolls_por_candidato" not in cfg
    assert cfg["headless"] is False


def test_filtrar_redes_acota_y_valida():
    cfg = {"redes": {"twitter": {"a": 1}, "tiktok": {"b": 2}}}
    assert run.filtrar_redes(dict(cfg), "tiktok")["redes"] == {"tiktok": {"b": 2}}
    assert run.filtrar_redes(dict(cfg), None)["redes"] == cfg["redes"]
    import pytest
    with pytest.raises(SystemExit):
        run.filtrar_redes(dict(cfg), "instagram")
```

2. Actualizar `test_armar_bloque_mapea_al_shape_de_electoral`: los IDs pasan de `Post_0`/`Post_1` a `twitter_0`/`twitter_1` (reemplazar en las aserciones).

3. `test_armar_payload_reusa_electoral`: `armar_payload` ahora recibe una LISTA — `run.armar_payload([bloque], ["warn-previo"])` y `meta["bloques"] == [bloque["status"]]` queda igual.

4. Agregar el test multi-red de payload:

```python
POST_TIKTOK = {"texto": "Milei imparable en TikTok", "autor": "@tt", "fecha": "1 d",
               "red": "tiktok", "es_electoral": True,
               "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.8}],
               "cita": "imparable"}


def test_armar_payload_fusiona_bloques_por_red():
    b_tw = run.armar_bloque([POST_MILEI], "twitter", busquedas=1, crudos=1, errores=0)
    b_tt = run.armar_bloque([POST_TIKTOK], "tiktok", busquedas=1, crudos=2, errores=1)
    payload = run.armar_payload([b_tw, b_tt], [])
    assert payload["candidatos"][0]["nombre"] == "Javier Milei"
    assert payload["candidatos"][0]["por_red"] == {"twitter": 1, "tiktok": 1}
    meta = payload["meta"]
    assert meta["total_posts"] == 2 and meta["posts_electorales"] == 2
    assert [b["red"] for b in meta["bloques"]] == ["twitter", "tiktok"]


def test_author_url_por_red():
    assert run._author_url("@fan", "twitter") == "https://x.com/fan"
    assert run._author_url("@fan", "tiktok") == "https://www.tiktok.com/@fan"
    assert run._author_url("Nombre Visible", "tiktok") == ""
```

5. Adaptar los mocks de `correr` a multi-red: en `_preparar_correr`, `capturar_candidato` gana el parámetro `red_nombre` al frente y los pools son por red:

```python
    monkeypatch.setattr(run.accounts, "cargar_pool", lambda ruta=None, red="twitter": pool)
    monkeypatch.setattr(run.accounts, "guardar_pool", lambda p, ruta=None, red="twitter": None)
    monkeypatch.setattr(run, "capturar_candidato",
                        lambda red_nombre, cfg, pool, cand, carpeta, warnings: [
                            {"ruta": tmp_path / f"{red_nombre}-{cand}.png", "contexto": ""}])
    monkeypatch.setattr(run.vision, "read_capture",
                        lambda ruta, red, candidatos=None, contexto="": posts_por_captura)
```

   (los `_cfg(...)` de los tests siguen funcionando porque `cargar_config` de un archivo inexistente ya devuelve el formato nuevo.)

6. Agregar el test de corrida multi-red:

```python
def test_correr_multi_red_arma_un_bloque_por_red(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    # vision devuelve el post según la red que se está capturando:
    monkeypatch.setattr(run.vision, "read_capture",
                        lambda ruta, red, candidatos=None, contexto="":
                        [POST_MILEI] if red == "twitter" else [POST_TIKTOK])
    cfg = _cfg(candidatos=["Javier Milei"])
    cfg["redes"] = {"twitter": {"scrolls_por_candidato": 3},
                    "tiktok": {"scrolls_por_candidato": 3, "videos_comentarios": 2,
                               "scrolls_comentarios": 2, "candidatos_comentarios": []}}
    rc = run.correr(cfg, dry_run=False)
    assert rc == 0 and len(escritos) == 1
    assert [b["red"] for b in escritos[0]["meta"]["bloques"]] == ["twitter", "tiktok"]
    assert escritos[0]["candidatos"][0]["por_red"] == {"twitter": 1, "tiktok": 1}
```

7. En los dos tests de `capturar_candidato` (rotación y error inesperado), la llamada gana la red al frente: `run.capturar_candidato("twitter", _cfg(), pool, "Javier Milei", tmp_path, warnings)`. El monkeypatch del fake sigue siendo sobre `twitter.capturar` (Task 4).

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local\test_run.py`
Expected: FAIL — varios (`KeyError: 'redes'`, `AttributeError: filtrar_redes`, firmas).

- [ ] **Step 3: Implementación**

En `scraper_local/run.py`:

1. `CONFIG_DEFAULT` y `cargar_config`:

```python
CONFIG_DEFAULT = {
    "redes": {"twitter": {"scrolls_por_candidato": 3}},
    "candidatos": None,  # None = electoral.CANDIDATOS_DEFAULT
    "espera_entre_scrolls": [2, 5],
    "espera_entre_candidatos": [20, 40],
    "viewport": [950, 1300],
    "headless": True,
    "min_posts_electorales": 1,
    "conservar_corridas": 3,
}


def cargar_config(ruta=None) -> dict:
    cfg = dict(CONFIG_DEFAULT)
    ruta = Path(ruta) if ruta else RUTA_CONFIG
    if ruta.exists():
        cfg.update(json.loads(ruta.read_text(encoding="utf-8")))
    # Formato legacy (Fase 3): "red" + "scrolls_por_candidato" planos.
    if "red" in cfg:
        cfg["redes"] = {cfg.pop("red"): {
            "scrolls_por_candidato": cfg.pop("scrolls_por_candidato", 3)}}
    return cfg


def filtrar_redes(cfg: dict, redes_csv: str | None) -> dict:
    """Acota cfg["redes"] a las de --redes (coma-separado). Aborta si no queda ninguna."""
    if redes_csv:
        pedidas = {r.strip() for r in redes_csv.split(",") if r.strip()}
        cfg["redes"] = {k: v for k, v in cfg["redes"].items() if k in pedidas}
    if not cfg["redes"]:
        sys.exit(f"--redes '{redes_csv}': ninguna red del config coincide.")
    return cfg
```

2. `_author_url` y `armar_bloque` (IDs por red):

```python
def _author_url(autor: str, red: str) -> str:
    if not autor.startswith("@"):
        return ""
    if red == "twitter":
        return f"https://x.com/{autor[1:]}"
    if red == "tiktok":
        return f"https://www.tiktok.com/{autor}"
    return ""
```

   En `armar_bloque`, la línea `pid = f"Post_{i}"` pasa a `pid = f"{red}_{i}"` (los IDs no pueden colisionar entre bloques al fusionar).

3. `armar_payload` recibe la lista de bloques:

```python
def armar_payload(bloques: list[dict], warnings: list[str]) -> dict:
    """Arma el payload del snapshot con la MISMA forma que electoral.run_boca_de_urna."""
    candidatos, baja_conf, posts_by_id, analysis = electoral._merge_bloques(bloques)
    evidencia = electoral.build_evidence(analysis, posts_by_id)
    comparacion, comp_warnings = electoral.compare_vs_pollsters(candidatos, [])
    warnings = list(warnings) + list(comp_warnings)
    if baja_conf:
        warnings.append(
            f"{baja_conf} mención(es) descartada(s) por baja confianza (< {electoral.CONF_MIN}).")
    posts_electorales = sum(1 for a in analysis if a.get("es_electoral"))
    return {
        "candidatos": candidatos, "evidencia": evidencia, "comparacion": comparacion,
        "meta": {"total_posts": sum(b["status"]["encontrados"] for b in bloques),
                 "posts_electorales": posts_electorales,
                 "analizados": sum(b["status"]["analizados"] for b in bloques),
                 "bloques": [b["status"] for b in bloques],
                 "disclaimer": electoral.DISCLAIMER, "warnings": warnings},
    }
```

4. `capturar_candidato` parametrizado por red (generaliza el interino de la Task 4):

```python
def capturar_candidato(red_nombre: str, cfg: dict, pool: dict, candidato: str,
                       carpeta: Path, warnings: list[str]) -> list[dict]:
    """Capturas de UN candidato en UNA red, rotando la cuenta UNA vez si se quema.
    Devuelve [] si no se pudo (el resto de la corrida sigue)."""
    red_mod = redes.POR_NOMBRE[red_nombre]
    for _intento in range(2):  # cuenta actual + una rotación
        cuenta = accounts.proxima_cuenta(pool)
        if cuenta is None:
            warnings.append(f"[{red_nombre}] Sin cuentas activas: '{candidato}' quedó sin capturar.")
            return []
        try:
            capturas = red_mod.capturar(
                sesion=accounts.ruta_sesion(cuenta["alias"], red_nombre), termino=candidato,
                cfg=cfg, cfg_red=cfg["redes"][red_nombre], carpeta=carpeta,
                prefijo=f"{red_nombre}-{_slug(candidato)}", warnings=warnings)
            accounts.registrar_uso(pool, cuenta["alias"])
            return capturas
        except browser.SesionInvalidaError as e:
            log.warning("[%s] Cuenta '%s' quemada/challenge: %s", red_nombre, cuenta["alias"], e)
            accounts.marcar_quemada(pool, cuenta["alias"])
            warnings.append(f"[{red_nombre}] Cuenta '{cuenta['alias']}' marcada como quemada.")
        except Exception as e:
            log.error("[%s] Error inesperado capturando '%s': %s", red_nombre, candidato, e)
            warnings.append(f"[{red_nombre}] '{candidato}' quedó sin capturar (error del navegador: {e}).")
            return []
    warnings.append(f"[{red_nombre}] '{candidato}' quedó sin capturar (dos cuentas fallaron).")
    return []
```

5. `correr` itera las redes (reemplaza el cuerpo entre `pool = accounts.cargar_pool()` y `posts = dedup.dedup_posts(...)` inclusive):

```python
    candidatos = cfg.get("candidatos") or electoral.CANDIDATOS_DEFAULT
    bloques: list[dict] = []
    for red_nombre in cfg["redes"]:
        if red_nombre not in redes.POR_NOMBRE:
            warnings.append(f"Red desconocida en config: '{red_nombre}' (se saltea).")
            continue
        pool = accounts.cargar_pool(red=red_nombre)
        crudos_red: list[dict] = []
        errores = 0
        for i, candidato in enumerate(candidatos):
            log.info("[%s] Candidato %d/%d: %s", red_nombre, i + 1, len(candidatos), candidato)
            capturas = capturar_candidato(red_nombre, cfg, pool, candidato, carpeta, warnings)
            for cap in capturas:
                leidos = vision.read_capture(cap["ruta"], red_nombre, candidatos,
                                             contexto=cap["contexto"])
                if leidos is None:
                    errores += 1
                    warnings.append(f"Lectura fallida (Gemini) de {cap['ruta'].name}.")
                    continue
                crudos_red.extend(leidos)
            if i + 1 < len(candidatos) and capturas:
                browser.esperar_aleatorio(tuple(cfg["espera_entre_candidatos"]))
        accounts.guardar_pool(pool, red=red_nombre)
        posts_red = dedup.dedup_posts(crudos_red)
        log.info("[%s] Posts: %d crudos, %d tras dedup, %d errores de lectura.",
                 red_nombre, len(crudos_red), len(posts_red), errores)
        bloques.append(armar_bloque(posts_red, red_nombre, busquedas=len(candidatos),
                                    crudos=len(crudos_red), errores=errores))
    payload = armar_payload(bloques, warnings)
```

   (el resto de `correr` — dry-run, `min_posts_electorales`, subida, limpieza — queda igual.)

6. CLI en `main`:

```python
    ap.add_argument("--redes", default=None,
                    help="coma-separado (ej. twitter,tiktok); acota la corrida a esas redes")
```

   y después de `cfg = cargar_config(args.config)`: `cfg = filtrar_redes(cfg, args.redes)`.

7. Actualizar el docstring del módulo (primer párrafo): "Por red × candidato: capturas (browser/redes, rotando cuentas si se queman) → lectura con Gemini visión → dedup por red → un bloque por red → agregación REUSANDO la lógica del backend (electoral._merge_bloques fusiona y calcula por_red) → snapshot a Supabase."

8. Reemplazar `scraper_local/config.json` con el formato nuevo:

```json
{
  "redes": {
    "twitter": { "scrolls_por_candidato": 3 },
    "tiktok": {
      "scrolls_por_candidato": 3,
      "videos_comentarios": 2,
      "scrolls_comentarios": 2,
      "candidatos_comentarios": [
        "Javier Milei",
        "Axel Kicillof",
        "Sergio Massa",
        "Patricia Bullrich",
        "Cristina Fernández de Kirchner"
      ]
    }
  },
  "candidatos": null,
  "espera_entre_scrolls": [2, 5],
  "espera_entre_candidatos": [20, 40],
  "viewport": [950, 1300],
  "headless": true,
  "min_posts_electorales": 1,
  "conservar_corridas": 3
}
```

- [ ] **Step 4: Correr la suite completa**

Run: `backend\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scraper_local/run.py scraper_local/config.json scraper_local/test_run.py
git commit -m "feat(scraper): run.py multi-red — un bloque por red, --redes, config redes{}"
```

---

### Task 8: `run.ps1 -Redes`, README y testdata de TikTok

**Files:**
- Modify: `scraper_local/run.ps1`
- Modify: `scraper_local/README.md`
- Create: `scraper_local/testdata/busqueda_falsa_tiktok.html`
- Modify: `scraper_local/testdata/README.md`

**Interfaces:**
- Consumes: `run.py --redes` (Task 7).
- Produces: `run.ps1 [-Redes "twitter,tiktok"]` (lo usa la Task 9 para el Task Scheduler).

- [ ] **Step 1: `run.ps1` con parámetro `-Redes`**

Reemplazar la última sección de `scraper_local/run.ps1` (la línea `& ... run.py` y el exit):

```powershell
param([string]$Redes = "")
```

(el `param` va como PRIMERA línea del archivo, antes del comentario actual) y al final:

```powershell
$argumentos = @("run.py")
if ($Redes) { $argumentos += @("--redes", $Redes) }
& "$PSScriptRoot\.venv\Scripts\python.exe" @argumentos
exit $LASTEXITCODE
```

- [ ] **Step 2: Verificar run.ps1 sin tocar X real**

Run (desde `scraper_local/`): `powershell -NoProfile -File run.ps1 -Redes instagram`
Expected: termina con el mensaje `--redes 'instagram': ninguna red del config coincide.` y `$LASTEXITCODE` distinto de 0 — probamos el pasamanos de argumentos sin lanzar el scraper.

- [ ] **Step 3: Testdata de TikTok (para tuning y smoke de visión)**

Crear `scraper_local/testdata/busqueda_falsa_tiktok.html` — una página estática estilo búsqueda de TikTok con 3 cards (`data-e2e="search_top-item"`, cada una con `<a href="https://www.tiktok.com/@user/video/N">`, descripción con texto electoral inventado y autor visible) y un card "Promocionado". Sirve para: (a) dry-run de la maquinaria con `browser.py --url`, (b) smoke de visión screenshoteándola, (c) tuning de `links_de_videos` contra HTML real de referencia. Contenido inventado y conocido, mismo espíritu que `feed_falso.html` (posturas variadas para verificar el prompt).

Agregar a `scraper_local/testdata/README.md` una sección "TikTok falso" con el comando del smoke:

```
backend\.venv\Scripts\python.exe scraper_local\vision.py <captura-del-html> --red tiktok
```

y la tabla de resultados esperados según el contenido inventado del HTML.

- [ ] **Step 4: Actualizar `scraper_local/README.md`**

- Estado: `✅ Fase 4a: TikTok (redes/tiktok.py) — búsqueda + comentarios top de punteros` y `⏳ Fase 4b: Instagram`.
- Sección de cuentas: agregar `python accounts.py login tt1 --red tiktok` y `python accounts.py estado --red tiktok`.
- Sección de config: el formato `"redes": {...}` con las claves de tiktok explicadas (`videos_comentarios`, `scrolls_comentarios`, `candidatos_comentarios`) y la nota de cuota: TikTok solo en la corrida de las 16:00 (~56 llamadas extra de visión; el día queda ~128, bajo el techo medido el 2026-09-23).
- Sección Task Scheduler: dos tareas — 10:00 `-Redes twitter`, 16:00 `-Redes twitter,tiktok` (comandos exactos en la Task 9 del plan).
- Nota de tuning: los selectores `TUNEAR` de `redes/tiktok.py` y cómo probarlos (`run.py --dry-run --redes tiktok` con un config de 1 candidato).

- [ ] **Step 5: Suite + commit**

Run: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: PASS

```bash
git add scraper_local/run.ps1 scraper_local/README.md scraper_local/testdata/
git commit -m "feat(scraper): run.ps1 -Redes, README Fase 4 y testdata de TikTok"
```

---

### Task 9: Task Scheduler (2 tareas) + verificación en vivo

**Files:** ninguno del repo (configuración de esta PC + verificación manual).

**Interfaces:**
- Consumes: `run.ps1 -Redes` (Task 8), cuentas TikTok logueadas (paso manual del usuario).

- [ ] **Step 1: Login de las cuentas de TikTok (MANUAL del usuario)**

El usuario crea 2 cuentas descartables de TikTok y las loguea (navegador visible, detección automática):

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local"
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe accounts.py login tt1 --red tiktok
.venv\Scripts\python.exe accounts.py login tt2 --red tiktok
.venv\Scripts\python.exe accounts.py estado --red tiktok
```

Expected: `- tt1: activa · sesión OK · …` (y tt2 ídem).

- [ ] **Step 2: Probe dry-run de TikTok con 1 candidato (tuning de selectores)**

Config de prueba en el scratchpad con 1 candidato y comentarios activados
(`candidatos_comentarios` = ese candidato, `videos_comentarios: 1`), y correr:

```powershell
# cargar env vars del backend\.env como siempre, luego:
.venv\Scripts\python.exe run.py --dry-run --redes tiktok --config <ruta-probe-config>
```

Expected: capturas en `capturas/<timestamp>/tiktok-*.png` (búsqueda Y `-comentarios-`) y el meta impreso con `bloques[0].red == "tiktok"`. Acá es donde los selectores `TUNEAR` se ajustan contra el DOM real (esperar 2-3 iteraciones). Inspeccionar los PNG a ojo: ¿se ven cards con texto? ¿el panel de comentarios se lee?

- [ ] **Step 3: Dry-run completo multi-red**

```powershell
.venv\Scripts\python.exe run.py --dry-run
```

Expected: exit 0; el meta muestra DOS bloques (twitter y tiktok) con `encontrados > 0` en ambos.

- [ ] **Step 4: Reemplazar la tarea programada por dos (10hs solo X, 16hs X+TikTok)**

```powershell
$ruta = "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local\run.ps1"
Unregister-ScheduledTask -TaskName "SMATA Boca de Urna - Scraper" -Confirm:$false
$a10 = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -File `"$ruta`" -Redes twitter"
$a16 = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -File `"$ruta`" -Redes twitter,tiktok"
Register-ScheduledTask -TaskName "SMATA Boca de Urna - Scraper 10hs" -Action $a10 -Trigger (New-ScheduledTaskTrigger -Daily -At 10:00)
Register-ScheduledTask -TaskName "SMATA Boca de Urna - Scraper 16hs" -Action $a16 -Trigger (New-ScheduledTaskTrigger -Daily -At 16:00)
Get-ScheduledTask | Where-Object { $_.TaskName -like "SMATA*" } | Select-Object TaskName, State
```

Expected: las dos tareas nuevas en estado `Ready` y la vieja ausente.

- [ ] **Step 5: Corrida real y verificación end-to-end**

```powershell
Start-ScheduledTask -TaskName "SMATA Boca de Urna - Scraper 16hs"
# al terminar (~50-70 min), verificar el snapshot:
```

Expected: el endpoint de producción (`https://social-media-filter-engine.onrender.com/api/boca-de-urna`, modo stored) devuelve `meta.ultima_actualizacion` de hoy y `meta.bloques` con `twitter` Y `tiktok`; la app en `https://social-media-filter-engine.vercel.app/boca-de-urna` muestra el desglose por red.

- [ ] **Step 6: Push**

```bash
git push
```

Expected: Render redeploya (sin cambios de backend, pero el repo queda al día); verificar `commit` vivo en `https://social-media-filter-engine.onrender.com/`.

---

## Self-Review (hecho al escribir el plan)

- **Spec coverage:** contrato por red (T4/T6), accounts por red sin migración (T5), vision contexto (T1), dedup por red (T2), run multi-red + config + `--redes` (T7), run.ps1/Task Scheduler 10-16hs (T8/T9), testing con mocks + testdata (todas), tuning en vivo (T9). Backend/frontend: sin tareas — el spec pide cero cambios.
- **Colisión de IDs al fusionar bloques** (`Post_0` en ambos bloques pisaría posts en `_merge_bloques`): resuelto en T7 con IDs `f"{red}_{i}"` — mismo patrón que `electoral.run_network_block`.
- **Type consistency:** `capturar(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings) -> list[{"ruta","contexto"}]` idéntico en T4 (twitter), T6 (tiktok) y call sites de T4/T7. `read_capture(..., contexto="")` consistente T1/T4/T7. `cargar_pool(ruta=None, red=...)` consistente T5/T7.
