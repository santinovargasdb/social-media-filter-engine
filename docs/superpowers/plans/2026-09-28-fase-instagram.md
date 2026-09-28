# Fase Instagram — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar Instagram como tercera red al scraper local: búsqueda por sidebar + posts individuales para los 5 punteros, módulo `redes/instagram.py` con el mismo contrato que twitter y tiktok, sin cambios en backend ni frontend.

**Architecture:** El paquete `redes/` tiene un módulo por red; `run.py` itera las redes del config sin saber nada de cada red. `instagram.py` navega Home → sidebar search → tipeo humano → pestaña Posts → extrae links de posts vía DOM → abre y captura posts para punteros. Solo se llama a Gemini para posts abiertos (no para la grilla, cuyos thumbnails no tienen texto). La cuota diaria sube de ~128 a ~143 llamadas.

**Tech Stack:** Python 3.12, Playwright (importado diferido), `accounts.py` multi-red (ya existente), `browser.py` común (sin cambios), `redes/__init__.py`.

## Global Constraints

- Playwright se importa SIEMPRE diferido (dentro de funciones): `from playwright.sync_api import ...` debe estar DENTRO de `capturar()`, nunca al nivel del módulo. La suite corre sin Playwright instalado.
- Misma firma que twitter y tiktok: `capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict, carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]` donde cada dict es `{"ruta": Path, "contexto": str}`.
- `SesionInvalidaError` se lanza ante redirect a login/challenge; fallas de post individual degradan a warning.
- Tests se corren desde `scraper_local/` con el venv del scraper: `Set-Location scraper_local; .\.venv\Scripts\python.exe -m pytest <archivo> -v`
- Todos los selectores de Playwright marcados `# TUNEAR` al tope del módulo.
- Spec: `docs/superpowers/specs/2026-09-28-fase-instagram-design.md`

---

## File Map

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `scraper_local/redes/instagram.py` | **Crear** | Módulo Instagram: constantes, login_completado, links_de_posts, capturar |
| `scraper_local/redes/__init__.py` | **Modificar** | Registrar instagram en POR_NOMBRE |
| `scraper_local/test_redes_instagram.py` | **Crear** | Unit tests de instagram.py (partes puras) |
| `scraper_local/run.py` | **Modificar** | Agregar instagram a `_author_url` |
| `scraper_local/test_run.py` | **Modificar** | Agregar POST_INSTAGRAM + test de 3 bloques |
| `scraper_local/config.json` | **Modificar** | Agregar bloque "instagram" bajo "redes" |
| `scraper_local/testdata/ig_grilla_falsa.html` | **Crear** | Grilla falsa de búsqueda IG (dry-run + tuning visual) |
| `scraper_local/testdata/ig_post_falso.html` | **Crear** | Post IG falso con caption y comentarios (dry-run + tuning visual) |
| `scraper_local/testdata/README.md` | **Modificar** | Agregar sección de testdata de Instagram |

---

### Task 1: Testdata HTML y test_redes_instagram.py (tests que fallan primero)

**Files:**
- Create: `scraper_local/testdata/ig_grilla_falsa.html`
- Create: `scraper_local/testdata/ig_post_falso.html`
- Create: `scraper_local/test_redes_instagram.py`

**Interfaces:**
- Produce: `instagram.login_completado(url: str) -> bool`
- Produce: `instagram.links_de_posts(hrefs: list[str], cantidad: int) -> list[str]`
- Produce: `redes.POR_NOMBRE["instagram"]` apuntando al módulo

- [ ] **Step 1: Crear ig_grilla_falsa.html**

```
scraper_local/testdata/ig_grilla_falsa.html
```

Contenido completo:

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Instagram Búsqueda Falsa — Milei</title>
  <style>
    body { margin: 0; background: #fafafa; font-family: -apple-system, sans-serif; }
    header { border-bottom: 1px solid #eee; padding: 10px 20px; }
    nav { padding: 6px 20px; }
    nav a { margin-right: 12px; text-decoration: none; color: #333; font-size: 14px; }
    .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3px;
            max-width: 600px; margin: 12px auto; }
    .cell { aspect-ratio: 1; background: #ccc; position: relative; }
    .cell a { display: block; width: 100%; height: 100%; }
    .cell span { position: absolute; bottom: 6px; left: 6px; font-size: 11px;
                 background: rgba(0,0,0,.5); color: white; padding: 2px 4px; }
  </style>
</head>
<body>
  <header><strong>Instagram</strong> &nbsp; Resultados para: "Milei"</header>
  <nav>
    <a href="/milei/">Cuentas</a>
    <a href="#posts" id="tab-posts">Posts</a>
  </nav>
  <article class="grid">
    <!-- 3 posts válidos (href contiene /p/) -->
    <div class="cell" style="background:#a8d8a8">
      <a href="https://www.instagram.com/p/AAA111/"><span>@LibertyFanPageIG — Viva la libertad carajo #Milei</span></a>
    </div>
    <div class="cell" style="background:#f4a261">
      <a href="https://www.instagram.com/p/BBB222/"><span>@AnalisisElectoralIG — Milei sube en encuestas</span></a>
    </div>
    <div class="cell" style="background:#e9c46a">
      <a href="https://www.instagram.com/p/CCC333/"><span>@DataPolitica2026 — Milei vs Kicillof hoy</span></a>
    </div>
    <!-- 1 enlace de perfil (sin /p/ — debe ser filtrado por links_de_posts) -->
    <div class="cell" style="background:#bbb">
      <a href="https://www.instagram.com/milfanpage_oficial/"><span>Perfil (no es post)</span></a>
    </div>
  </article>
</body>
</html>
```

- [ ] **Step 2: Crear ig_post_falso.html**

```
scraper_local/testdata/ig_post_falso.html
```

Contenido completo:

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Instagram Post Falso — Milei</title>
  <style>
    body { margin: 0; background: #fafafa; font-family: -apple-system, sans-serif; }
    .post { max-width: 600px; margin: 20px auto; border: 1px solid #eee;
            background: white; border-radius: 8px; overflow: hidden; }
    .post-header { display: flex; align-items: center; padding: 12px; gap: 10px; }
    .avatar { width: 32px; height: 32px; border-radius: 50%; background: #c44; }
    .img-placeholder { width: 100%; height: 300px; background: #a8d8a8;
                       display: flex; align-items: center; justify-content: center;
                       font-size: 18px; color: #555; }
    .caption { padding: 12px; font-size: 14px; line-height: 1.5; }
    .comments { border-top: 1px solid #eee; padding: 12px; }
    .comment { margin-bottom: 10px; font-size: 13px; }
    .comment strong { margin-right: 4px; }
  </style>
</head>
<body>
  <div class="post">
    <div class="post-header">
      <div class="avatar"></div>
      <strong>@LibertyFanPageIG</strong>
    </div>
    <div class="img-placeholder">[ Imagen del post ]</div>
    <div class="caption">
      <strong>@LibertyFanPageIG</strong> Viva la libertad carajo 🦁
      El cambio que necesitaba Argentina. Milei es la mejor opción para 2026.
      #Milei #LibertadAvanza #Argentina2026
    </div>
    <div class="comments">
      <div class="comment">
        <strong>@PatriotaArgentino</strong>
        Así es! El mejor presidente de la historia 🇦🇷 Lo voto seguro.
      </div>
      <div class="comment">
        <strong>@KirchneristaFiel</strong>
        Un desastre total, arruinó la economía con sus medidas. En contra desde el día 1.
      </div>
      <div class="comment">
        <strong>@ObservadorNeutral</strong>
        Hay que ver los resultados después de un año de gestión. Los números son mixtos.
      </div>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 3: Escribir test_redes_instagram.py con tests que fallan**

```python
# scraper_local/test_redes_instagram.py
"""Tests de redes/instagram.py — partes puras (selectores reales se tunean en vivo)."""
import redes
from redes import instagram


def test_registro_incluye_instagram():
    assert redes.POR_NOMBRE["instagram"] is instagram


def test_login_completado_fuera_de_login_y_challenge():
    assert instagram.login_completado("https://www.instagram.com/") is True
    assert instagram.login_completado("https://www.instagram.com/p/ABC123/") is True
    assert instagram.login_completado("https://www.instagram.com/explore/") is True
    assert instagram.login_completado("https://www.instagram.com/accounts/login/") is False
    assert instagram.login_completado("https://www.instagram.com/accounts/login/?next=/") is False
    assert instagram.login_completado("https://www.instagram.com/challenge/") is False
    assert instagram.login_completado("https://www.instagram.com/challenge/action/") is False


def test_links_de_posts_filtra_dedupea_y_corta():
    hrefs = [
        "https://www.instagram.com/p/AAA111/",
        "https://www.instagram.com/milfanpage_oficial/",   # perfil: afuera
        "https://www.instagram.com/p/AAA111/",             # repetido: afuera
        "https://www.instagram.com/p/BBB222/",
        "https://www.instagram.com/p/CCC333/",
    ]
    assert instagram.links_de_posts(hrefs, 2) == [
        "https://www.instagram.com/p/AAA111/",
        "https://www.instagram.com/p/BBB222/",
    ]


def test_links_de_posts_menos_que_pedidos():
    assert instagram.links_de_posts(["https://www.instagram.com/p/AAA/"], 5) == [
        "https://www.instagram.com/p/AAA/"
    ]
    assert instagram.links_de_posts([], 3) == []
```

- [ ] **Step 4: Verificar que los tests fallan**

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local"
.\.venv\Scripts\python.exe -m pytest test_redes_instagram.py -v
```

Expected: `ERROR` o `ImportError: cannot import name 'instagram' from 'redes'`

---

### Task 2: redes/instagram.py + redes/__init__.py

**Files:**
- Create: `scraper_local/redes/instagram.py`
- Modify: `scraper_local/redes/__init__.py` (línea 8: agregar instagram al dict POR_NOMBRE)

**Interfaces:**
- Consumes: `browser.pagina_con_sesion`, `browser.capturar_pagina`, `browser.esperar_aleatorio`, `browser.SesionInvalidaError`, `browser.TIMEOUT_FEED_MS`
- Produces: `instagram.LOGIN_URL: str`, `instagram.login_completado(url: str) -> bool`, `instagram.links_de_posts(hrefs: list[str], cantidad: int) -> list[str]`, `instagram.capturar(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings) -> list[dict]`

- [ ] **Step 1: Crear redes/instagram.py**

```python
# scraper_local/redes/instagram.py
"""Búsqueda en Instagram vía sidebar search + posts individuales para punteros.

Flujo: Home → click icono búsqueda sidebar → tipeo con delay humano →
pestaña Posts → extrae links de posts vía DOM (sin Gemini sobre la grilla,
los thumbnails no tienen texto) → abre posts de punteros y los captura.

Los selectores REALES se tunean en la PC de la oficina (marcados TUNEAR)."""
from pathlib import Path

import browser

IG_HOME_URL = "https://www.instagram.com/"
LOGIN_URL = "https://www.instagram.com/accounts/login/"

SELECTOR_SEARCH_ICON  = "[aria-label='Buscar']"          # icono lupa sidebar  # TUNEAR
SELECTOR_SEARCH_INPUT = "input[placeholder*='Buscar']"   # input del sidebar   # TUNEAR
SELECTOR_TAB_POSTS    = "text=Posts"                     # pestaña de Posts    # TUNEAR
SELECTOR_POST_GRID    = "article"                        # grid de resultados  # TUNEAR
SELECTOR_POST_LINKS   = "a[href*='/p/']"                 # links a posts /p/   # TUNEAR
MARCAS_SESION_MUERTA  = ("/accounts/login/", "/challenge/")


def login_completado(url: str) -> bool:
    """IG sale de /accounts/login/ y /challenge/ al terminar el login manual."""
    return "/accounts/login/" not in url and "/challenge/" not in url


def links_de_posts(hrefs: list[str], cantidad: int) -> list[str]:
    """Primeros `cantidad` links de post únicos (contienen /p/), en orden de aparición."""
    out: list[str] = []
    for h in hrefs:
        if "/p/" in h and h not in out:
            out.append(h)
        if len(out) == cantidad:
            break
    return out


def _verificar_sesion(page) -> None:
    if any(marca in page.url for marca in MARCAS_SESION_MUERTA):
        raise browser.SesionInvalidaError(f"redirigido a {page.url}")


def capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict,
             carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]:
    from playwright.sync_api import TimeoutError as PWTimeout  # diferido
    esperas = tuple(cfg["espera_entre_scrolls"])
    with browser.pagina_con_sesion(sesion, tuple(cfg["viewport"]), cfg["headless"]) as page:
        page.goto(IG_HOME_URL, timeout=browser.TIMEOUT_FEED_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        _verificar_sesion(page)

        # Sidebar search → tipeo humano → pestaña Posts
        page.locator(SELECTOR_SEARCH_ICON).first.click()
        browser.esperar_aleatorio((1, 2))
        page.locator(SELECTOR_SEARCH_INPUT).fill("")
        page.keyboard.type(termino, delay=80)
        browser.esperar_aleatorio((1, 2))
        try:
            page.locator(SELECTOR_TAB_POSTS).first.click()
            browser.esperar_aleatorio((2, 3))
        except Exception:
            pass

        try:
            page.wait_for_selector(SELECTOR_POST_GRID, timeout=browser.TIMEOUT_FEED_MS)
        except PWTimeout:
            warnings.append(
                f"[instagram] Grilla no apareció para '{termino}' (¿sin resultados?).")
            return []

        # Scroll para exponer más links antes de extraer
        for _ in range(cfg_red.get("scrolls_por_candidato", 1)):
            page.evaluate("window.scrollBy(0, window.innerHeight * 0.9)")
            browser.esperar_aleatorio(esperas)

        hrefs = page.eval_on_selector_all(SELECTOR_POST_LINKS, "els => els.map(e => e.href)")

        candidatos_posts = cfg_red.get("candidatos_posts") or []
        if termino not in candidatos_posts:
            return []  # candidato no puntero: sin Gemini para IG

        posts_por_candidato = cfg_red.get("posts_por_candidato", 3)
        post_links = links_de_posts(hrefs, posts_por_candidato * 2)[:posts_por_candidato]
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
```

- [ ] **Step 2: Actualizar redes/__init__.py**

Archivo actual (línea 7-9):
```python
from . import tiktok, twitter

POR_NOMBRE = {"twitter": twitter, "tiktok": tiktok}
```

Reemplazar por:
```python
from . import instagram, tiktok, twitter

POR_NOMBRE = {"instagram": instagram, "tiktok": tiktok, "twitter": twitter}
```

- [ ] **Step 3: Correr los tests — deben pasar**

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local"
.\.venv\Scripts\python.exe -m pytest test_redes_instagram.py -v
```

Expected:
```
test_redes_instagram.py::test_registro_incluye_instagram PASSED
test_redes_instagram.py::test_login_completado_fuera_de_login_y_challenge PASSED
test_redes_instagram.py::test_links_de_posts_filtra_dedupea_y_corta PASSED
test_redes_instagram.py::test_links_de_posts_menos_que_pedidos PASSED
4 passed
```

- [ ] **Step 4: Correr la suite completa para verificar que no se rompió nada**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: todos los tests pasan (misma cantidad que antes + 4 nuevos).

- [ ] **Step 5: Commit**

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT"
git add scraper_local/redes/instagram.py scraper_local/redes/__init__.py scraper_local/test_redes_instagram.py scraper_local/testdata/ig_grilla_falsa.html scraper_local/testdata/ig_post_falso.html
git commit -m @'
feat(scraper): redes/instagram.py — sidebar search + posts punteros

Módulo Instagram con el contrato de redes/: login_completado, links_de_posts
y capturar (Home → sidebar search → pestaña Posts → links DOM → posts
individuales para candidatos_posts). Selectores marcados TUNEAR.
4 unit tests verdes; testdata HTML para dry-run y tuning visual.
'@
```

---

### Task 3: run.py (_author_url) + test_run.py (3 bloques)

**Files:**
- Modify: `scraper_local/run.py` líneas 82-89 (`_author_url`)
- Modify: `scraper_local/test_run.py` (agregar POST_INSTAGRAM y test)

**Interfaces:**
- Consumes: `run._author_url(autor, red)` — debe devolver URL de Instagram para `@alias`
- Produces: `run._author_url("@fan", "instagram") == "https://www.instagram.com/fan/"`

- [ ] **Step 1: Escribir el test que falla primero**

Abrir `scraper_local/test_run.py` y agregar al final del archivo (después del último test):

```python
# --- Instagram ---

POST_INSTAGRAM = {
    "texto": "Milei imparable en Instagram", "autor": "@fan.ig", "fecha": "2 d",
    "red": "instagram", "es_electoral": True,
    "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.85}],
    "cita": "imparable"
}


def test_author_url_instagram():
    assert run._author_url("@fan.ig", "instagram") == "https://www.instagram.com/fan.ig/"
    assert run._author_url("Nombre Visible", "instagram") == ""


def test_correr_multi_red_tres_bloques(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    monkeypatch.setattr(
        run.vision, "read_capture",
        lambda ruta, red, candidatos=None, contexto="": {
            "twitter": [POST_MILEI],
            "tiktok": [POST_TIKTOK],
            "instagram": [POST_INSTAGRAM],
        }.get(red, []))
    cfg = _cfg(candidatos=["Javier Milei"])
    cfg["redes"] = {
        "twitter": {"scrolls_por_candidato": 3},
        "tiktok": {"scrolls_por_candidato": 3, "videos_comentarios": 2,
                   "scrolls_comentarios": 2, "candidatos_comentarios": []},
        "instagram": {"scrolls_por_candidato": 1, "posts_por_candidato": 3,
                      "candidatos_posts": []},
    }
    rc = run.correr(cfg, dry_run=False)
    assert rc == 0 and len(escritos) == 1
    assert [b["red"] for b in escritos[0]["meta"]["bloques"]] == ["twitter", "tiktok", "instagram"]
    assert escritos[0]["candidatos"][0]["por_red"] == {"twitter": 1, "tiktok": 1, "instagram": 1}
```

- [ ] **Step 2: Verificar que los 2 tests nuevos fallan**

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local"
.\.venv\Scripts\python.exe -m pytest test_run.py::test_author_url_instagram test_run.py::test_correr_multi_red_tres_bloques -v
```

Expected: ambos FAIL (el primero por `AssertionError`, el segundo porque `_author_url("@fan.ig", "instagram")` devuelve `""` en lugar de la URL).

- [ ] **Step 3: Actualizar _author_url en run.py**

Bloque actual en `run.py` líneas 82-89:
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

Reemplazar por:
```python
def _author_url(autor: str, red: str) -> str:
    if not autor.startswith("@"):
        return ""
    if red == "twitter":
        return f"https://x.com/{autor[1:]}"
    if red == "tiktok":
        return f"https://www.tiktok.com/{autor}"
    if red == "instagram":
        return f"https://www.instagram.com/{autor[1:]}/"
    return ""
```

- [ ] **Step 4: Correr los tests — deben pasar**

```powershell
.\.venv\Scripts\python.exe -m pytest test_run.py::test_author_url_instagram test_run.py::test_correr_multi_red_tres_bloques -v
```

Expected:
```
test_run.py::test_author_url_instagram PASSED
test_run.py::test_correr_multi_red_tres_bloques PASSED
```

- [ ] **Step 5: Correr la suite completa**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: todos pasan.

- [ ] **Step 6: Commit**

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT"
git add scraper_local/run.py scraper_local/test_run.py
git commit -m @'
feat(scraper): instagram en _author_url + test multi-red 3 bloques

_author_url ahora genera URLs de Instagram para @alias.
Test correr_multi_red_tres_bloques verifica que twitter+tiktok+instagram
producen 3 bloques y por_red correcto en el payload fusionado.
'@
```

---

### Task 4: config.json + testdata README + Task Scheduler

**Files:**
- Modify: `scraper_local/config.json`
- Modify: `scraper_local/testdata/README.md`

- [ ] **Step 1: Actualizar config.json**

Archivo actual bajo `"redes"`:
```json
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
```

Reemplazar por:
```json
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
  },
  "instagram": {
    "scrolls_por_candidato": 1,
    "posts_por_candidato": 3,
    "candidatos_posts": [
      "Javier Milei",
      "Axel Kicillof",
      "Sergio Massa",
      "Patricia Bullrich",
      "Cristina Fernández de Kirchner"
    ]
  }
},
```

- [ ] **Step 2: Agregar sección Instagram al testdata README**

Agregar al final de `scraper_local/testdata/README.md`:

```markdown
---

## Instagram falso (`ig_grilla_falsa.html` y `ig_post_falso.html`)

`ig_grilla_falsa.html` simula la grilla de resultados de Instagram con **3 posts**
(`href` con `/p/`) y **1 perfil** (sin `/p/` — debe ser filtrado por `links_de_posts`).

`ig_post_falso.html` simula un post individual con caption y 3 comentarios.

### Dry-run de la maquinaria de captura

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT"
scraper_local\.venv\Scripts\python.exe scraper_local\browser.py `
  --url "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local\testdata\ig_grilla_falsa.html" `
  --scrolls 2 --visible
```

### Smoke de visión sobre el post falso

Screenshotear `ig_post_falso.html` con Playwright o un navegador (ancho ~600px,
full-page), guardar como `ig_post_falso.png` y correr:

```powershell
Get-Content backend\.env | ForEach-Object {
  if ($_ -match '^([^=#]+)=(.*)$') { Set-Item "env:$($matches[1].Trim())" $matches[2].Trim() } }
$env:VISION_MIN_INTERVAL = "0"
backend\.venv\Scripts\python.exe scraper_local\vision.py `
  scraper_local\testdata\ig_post_falso.png --red instagram
```

### Resultados esperados (ig_post_falso.html)

| # | autor | es_electoral | candidatos (postura) |
|---|-------|--------------|----------------------|
| 1 | @LibertyFanPageIG (caption) | true | Javier Milei (a_favor) |
| 2 | @PatriotaArgentino | true | Javier Milei (a_favor) |
| 3 | @KirchneristaFiel | true | Javier Milei (en_contra) |
| 4 | @ObservadorNeutral | true | Javier Milei (neutro) |

Criterio: la caption y los comentarios se tratan como publicaciones separadas;
cero posts de UI (botones, headers). Si Gemini falla una postura, ajustar el
PROMPT en `vision.py`, no `REGLAS_CANDIDATOS`.
```

- [ ] **Step 3: Correr la suite completa una última vez**

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local"
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: todos los tests pasan.

- [ ] **Step 4: Commit config y README**

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT"
git add scraper_local/config.json scraper_local/testdata/README.md
git commit -m @'
feat(scraper): config instagram + README testdata

Agrega bloque "instagram" al config (scrolls_por_candidato:1, posts_por_candidato:3,
candidatos_posts: 5 punteros). Actualiza testdata/README con sección de dry-run y
smoke test para ig_grilla_falsa.html e ig_post_falso.html.
'@
```

- [ ] **Step 5: Push a origin**

```powershell
git push
```

- [ ] **Step 6: Actualizar la tarea del Task Scheduler de las 16:00** (paso manual en la PC)

Abrir el **Programador de tareas de Windows** → buscar la tarea **"SMATA Boca de Urna - Scraper 16:00"** → Propiedades → pestaña Acciones → Editar. El argumento actual es:

```
-NoProfile -File "C:\...\scraper_local\run.ps1" -Redes twitter,tiktok
```

Cambiarlo a:

```
-NoProfile -File "C:\...\scraper_local\run.ps1" -Redes twitter,tiktok,instagram
```

Guardar y verificar disparándola manualmente una vez en dry-run antes de que corra sola:

```powershell
Set-Location "C:\Users\accsoc\Desktop\Github Clone\Filtro-RedesSocialesSMT\scraper_local"
.\.venv\Scripts\python.exe run.py --dry-run --redes instagram
```

Expected: el meta impreso muestra el bloque de instagram (con 0 posts electorales para candidatos no punteros, o posts para los punteros si las cuentas de IG ya están logueadas). Si las cuentas no están logueadas aún, loguearlas primero:

```powershell
.\.venv\Scripts\python.exe accounts.py login ig1 --red instagram
.\.venv\Scripts\python.exe accounts.py login ig2 --red instagram
```
