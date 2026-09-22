# Fase 3 — Scraper de X (Playwright + cuentas + snapshot) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** El scraper local completo para X: cuentas descartables con login manual, capturas con Playwright, dedup, orquestación y subida del snapshot a Supabase.

**Architecture:** Cuatro módulos chicos en `scraper_local/` con una responsabilidad cada uno: `accounts.py` (pool + sesiones), `browser.py` (navegación + capturas; el ÚNICO que habla con Playwright), `dedup.py` (puro), `run.py` (orquesta y REUSA la agregación del backend: `electoral._merge_bloques` / `build_evidence` / `compare_vs_pollsters`, y `store.write_snapshot`). Los imports de Playwright son SIEMPRE diferidos (adentro de funciones) para que la suite corra sin Playwright instalado.

**Tech Stack:** Python (stdlib + requests; Playwright solo en la PC de la oficina), pytest con monkeypatch/tmp_path (sin red, sin browser).

**Spec:** `docs/superpowers/specs/2026-09-22-scraper-x-fase3-design.md`

## Global Constraints

- **El backend NO se toca**: ni `requirements.txt` del backend, ni `electoral.py`, ni `store.py`, ni `vision.py` (Fase 2 cerrada). Todo lo nuevo vive en `scraper_local/`.
- **Playwright con import diferido**: ningún `import playwright` a nivel módulo — solo adentro de las funciones que lo usan. La suite (`backend\.venv\Scripts\python.exe -m pytest -q scraper_local`) debe pasar SIN playwright instalado.
- **Login manual**: sin passwords guardados en ningún archivo. `accounts.json`, `.sesiones/`, `capturas/` y `logs/` van al `.gitignore` — JAMÁS commiteados.
- **Regla de seguridad**: si la corrida no junta `min_posts_electorales` posts electorales, o la subida falla, NO se pisa el snapshot anterior y el exit code es 1.
- Comentarios y docstrings en castellano rioplatense, como el resto del repo.
- Suites: backend (`cd backend; .venv\Scripts\python.exe -m pytest -q`) y scraper (desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`) verdes tras cada task.

## File Structure

- `scraper_local/dedup.py` (crear) — dedup puro por `(autor, hash texto)`.
- `scraper_local/accounts.py` (crear) — pool JSON + sesiones + CLI login/estado.
- `scraper_local/browser.py` (crear) — URL de búsqueda, espera aleatoria, captura por scroll, `SesionInvalidaError`, CLI dry-run.
- `scraper_local/run.py` (crear) + `scraper_local/config.json` (crear) — orquestación + mapping al shape de electoral + subida.
- `scraper_local/test_dedup.py`, `test_accounts.py`, `test_browser.py`, `test_run.py` (crear).
- `scraper_local/requirements.txt`, `run.ps1`, `accounts.example.json` (crear); `.gitignore` y `scraper_local/README.md` (modificar).

---

### Task 1: `dedup.py`

**Files:**
- Create: `scraper_local/dedup.py`
- Test: `scraper_local/test_dedup.py`

**Interfaces:**
- Consumes: nada.
- Produces: `dedup.dedup_posts(posts: list[dict]) -> list[dict]` — conserva la primera aparición; posts son dicts de visión (`autor`, `texto`, ...). Task 4 lo usa.

- [ ] **Step 1: Escribir los tests (fallan)**

Crear `scraper_local/test_dedup.py`:

```python
"""Tests de dedup.py — puro, sin I/O."""
import dedup


def test_dedup_conserva_primera_aparicion():
    posts = [
        {"autor": "@user", "texto": "Hola mundo", "fecha": "2 h"},
        {"autor": "@user", "texto": "Hola mundo", "fecha": "3 h"},  # duplicado exacto
        {"autor": "@otra", "texto": "Hola mundo", "fecha": "4 h"},  # otro autor: queda
    ]
    out = dedup.dedup_posts(posts)
    assert len(out) == 2
    assert out[0]["fecha"] == "2 h"  # la primera aparición gana
    assert out[1]["autor"] == "@otra"


def test_dedup_normaliza_autor_y_texto():
    posts = [
        {"autor": "@User", "texto": "Hola   mundo"},
        {"autor": "user", "texto": "hola mundo"},       # sin @, case y espacios distintos
        {"autor": "@USER", "texto": "  HOLA MUNDO  "},
    ]
    assert len(dedup.dedup_posts(posts)) == 1


def test_dedup_texto_largo_compara_primeros_200():
    base = "x" * 300
    posts = [
        {"autor": "@a", "texto": base + "cola-1"},
        {"autor": "@a", "texto": base + "cola-2"},  # difieren después del char 200
    ]
    assert len(dedup.dedup_posts(posts)) == 1


def test_dedup_sin_identidad_no_colapsa():
    posts = [
        {"autor": "", "texto": ""},
        {"autor": "", "texto": ""},
    ]
    assert len(dedup.dedup_posts(posts)) == 2  # sin autor ni texto no hay identidad


def test_dedup_lista_vacia():
    assert dedup.dedup_posts([]) == []
```

- [ ] **Step 2: Verificar que fallan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local/test_dedup.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'dedup'`.

- [ ] **Step 3: Implementar `scraper_local/dedup.py`**

```python
"""
Dedup de posts entre capturas solapadas (Fase 3 del scraper local).

El mismo post aparece en capturas consecutivas (el scroll se solapa ~10%) y en
búsquedas de candidatos distintos. Clave: (autor normalizado, hash del texto
normalizado). Puro (sin I/O): se testea completo en CI.
"""
import hashlib

# El texto visible de un post puede variar en la cola (links/menciones cortadas
# por el borde): comparar los primeros 200 chars alcanza para identificarlo.
_TEXTO_CHARS = 200


def _clave(post: dict, indice: int) -> tuple:
    autor = (post.get("autor") or "").strip().lower().lstrip("@")
    texto = " ".join((post.get("texto") or "").lower().split())[:_TEXTO_CHARS]
    if not autor and not texto:
        # Sin autor ni texto no hay identidad: que no colapsen entre sí.
        return ("", indice)
    return (autor, hashlib.md5(texto.encode("utf-8")).hexdigest()[:16])


def dedup_posts(posts: list[dict]) -> list[dict]:
    """Conserva la PRIMERA aparición de cada post (orden estable)."""
    vistos: set = set()
    out: list[dict] = []
    for i, post in enumerate(posts):
        k = _clave(post, i)
        if k in vistos:
            continue
        vistos.add(k)
        out.append(post)
    return out
```

- [ ] **Step 4: Verificar que pasan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: 15 passed (10 de vision + 5 nuevos).

- [ ] **Step 5: Commit**

```bash
git add scraper_local/dedup.py scraper_local/test_dedup.py
git commit -m "feat(scraper): dedup de posts entre capturas solapadas"
```

---

### Task 2: `accounts.py`

**Files:**
- Create: `scraper_local/accounts.py`
- Test: `scraper_local/test_accounts.py`

**Interfaces:**
- Consumes: nada.
- Produces (Task 4 las usa): `cargar_pool(ruta=None) -> dict`, `guardar_pool(pool, ruta=None) -> None`, `proxima_cuenta(pool) -> dict | None`, `registrar_uso(pool, alias) -> None`, `marcar_quemada(pool, alias) -> None`, `ruta_sesion(alias) -> Path`. CLI: `login <alias>` / `estado`.

- [ ] **Step 1: Escribir los tests (fallan)**

Crear `scraper_local/test_accounts.py`:

```python
"""Tests de accounts.py — pool y rotación, sin browser (el login manual no se testea)."""
from datetime import datetime

import accounts


def _pool(*cuentas):
    return {"cuentas": [dict(c) for c in cuentas]}


A1 = {"alias": "a1", "estado": "activa", "ultima_vez": "2026-09-20T10:00:00+00:00", "notas": ""}
A2 = {"alias": "a2", "estado": "activa", "ultima_vez": "2026-09-21T10:00:00+00:00", "notas": ""}
NUEVA = {"alias": "n1", "estado": "activa", "ultima_vez": "", "notas": ""}
QUEMADA = {"alias": "q1", "estado": "quemada", "ultima_vez": "", "notas": ""}


def test_cargar_pool_inexistente_devuelve_vacio(tmp_path):
    assert accounts.cargar_pool(tmp_path / "no-existe.json") == {"cuentas": []}


def test_roundtrip_guardar_cargar(tmp_path):
    ruta = tmp_path / "pool.json"
    pool = _pool(A1, QUEMADA)
    accounts.guardar_pool(pool, ruta)
    assert accounts.cargar_pool(ruta) == pool


def test_proxima_cuenta_prefiere_nunca_usada_y_mas_vieja():
    assert accounts.proxima_cuenta(_pool(A2, A1, NUEVA))["alias"] == "n1"
    assert accounts.proxima_cuenta(_pool(A2, A1))["alias"] == "a1"


def test_proxima_cuenta_excluye_quemadas_y_pool_agotado():
    assert accounts.proxima_cuenta(_pool(QUEMADA)) is None
    assert accounts.proxima_cuenta(_pool()) is None


def test_marcar_quemada_y_registrar_uso():
    pool = _pool(A1, A2)
    accounts.marcar_quemada(pool, "a1")
    assert pool["cuentas"][0]["estado"] == "quemada"
    accounts.registrar_uso(pool, "a2")
    # timestamp ISO parseable y no vacío
    assert datetime.fromisoformat(pool["cuentas"][1]["ultima_vez"])


def test_ruta_sesion():
    ruta = accounts.ruta_sesion("cuenta1")
    assert ruta.name == "cuenta1.json"
    assert ruta.parent.name == ".sesiones"
```

- [ ] **Step 2: Verificar que fallan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local/test_accounts.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'accounts'`.

- [ ] **Step 3: Implementar `scraper_local/accounts.py`**

```python
"""
Cuentas descartables de X para el scraper local (Fase 3).

Pool en accounts.json (GIT-IGNORED) con estado por cuenta; las cookies
(storage_state de Playwright) viven en .sesiones/<alias>.json (GIT-IGNORED).
El login es MANUAL una sola vez por cuenta — sin passwords guardados:

    python accounts.py login <alias>   # navegador visible, logueás a mano
    python accounts.py estado          # lista el pool

Playwright se importa DIFERIDO (solo lo usa el CLI de login): la suite corre
sin playwright instalado.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUTA_POOL = BASE_DIR / "accounts.json"
DIR_SESIONES = BASE_DIR / ".sesiones"

X_LOGIN_URL = "https://x.com/login"


def cargar_pool(ruta=None) -> dict:
    ruta = Path(ruta) if ruta else RUTA_POOL
    if not ruta.exists():
        return {"cuentas": []}
    return json.loads(ruta.read_text(encoding="utf-8"))


def guardar_pool(pool: dict, ruta=None) -> None:
    ruta = Path(ruta) if ruta else RUTA_POOL
    ruta.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def proxima_cuenta(pool: dict) -> dict | None:
    """La cuenta activa usada hace más tiempo ('' = nunca usada, va primero)."""
    activas = [c for c in pool.get("cuentas", []) if c.get("estado") == "activa"]
    if not activas:
        return None
    return sorted(activas, key=lambda c: c.get("ultima_vez") or "")[0]


def registrar_uso(pool: dict, alias: str) -> None:
    for c in pool.get("cuentas", []):
        if c.get("alias") == alias:
            c["ultima_vez"] = datetime.now(timezone.utc).isoformat()


def marcar_quemada(pool: dict, alias: str) -> None:
    for c in pool.get("cuentas", []):
        if c.get("alias") == alias:
            c["estado"] = "quemada"


def ruta_sesion(alias: str) -> Path:
    return DIR_SESIONES / f"{alias}.json"


def _login(alias: str) -> int:
    """Login manual: navegador visible; el operador loguea y aprieta Enter acá."""
    from playwright.sync_api import sync_playwright  # diferido: solo el CLI lo necesita
    DIR_SESIONES.mkdir(exist_ok=True)
    pool = cargar_pool()
    if not any(c.get("alias") == alias for c in pool.get("cuentas", [])):
        pool.setdefault("cuentas", []).append(
            {"alias": alias, "estado": "activa", "ultima_vez": "", "notas": ""})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(X_LOGIN_URL)
        print(f"Logueá la cuenta '{alias}' en la ventana del navegador.")
        input("Cuando estés adentro (se ve el timeline), apretá Enter acá... ")
        context.storage_state(path=str(ruta_sesion(alias)))
        browser.close()
    for c in pool["cuentas"]:
        if c["alias"] == alias:
            c["estado"] = "activa"
    guardar_pool(pool)
    print(f"Sesión guardada en {ruta_sesion(alias)}. Cuenta '{alias}' activa.")
    return 0


def _estado() -> int:
    pool = cargar_pool()
    if not pool.get("cuentas"):
        print("Pool vacío. Agregá cuentas con: python accounts.py login <alias>")
        return 0
    for c in pool["cuentas"]:
        sesion = "sesión OK" if ruta_sesion(c["alias"]).exists() else "SIN sesión"
        print(f"- {c['alias']}: {c['estado']} · {sesion} · última vez: {c.get('ultima_vez') or 'nunca'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cuentas descartables del scraper (login manual).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    login = sub.add_parser("login", help="login manual de una cuenta (navegador visible)")
    login.add_argument("alias")
    sub.add_parser("estado", help="lista el pool")
    args = ap.parse_args(argv)
    if args.cmd == "login":
        return _login(args.alias)
    return _estado()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verificar que pasan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add scraper_local/accounts.py scraper_local/test_accounts.py
git commit -m "feat(scraper): pool de cuentas descartables con login manual y rotacion por antiguedad"
```

---

### Task 3: `browser.py`

**Files:**
- Create: `scraper_local/browser.py`
- Test: `scraper_local/test_browser.py`

**Interfaces:**
- Consumes: nada de otras tasks.
- Produces (Task 4 las usa): `SesionInvalidaError(Exception)`, `url_busqueda(termino) -> str`, `esperar_aleatorio(rango: tuple) -> None`, `capturar_pagina(page, scrolls, esperas, carpeta: Path, prefijo) -> list[Path]`, `capturar_busqueda(sesion: Path, termino, scrolls=3, viewport=(950, 1300), headless=True, esperas=(2, 5), carpeta=Path("capturas"), prefijo="captura") -> list[Path]`. CLI dry-run: `python browser.py --url <URL> [--scrolls N] [--visible] [--out carpeta]`.

- [ ] **Step 1: Escribir los tests (fallan)**

Crear `scraper_local/test_browser.py`. `capturar_pagina` se testea con un page FALSO (sin Playwright); la navegación real de X no se testea (se tunea en la oficina):

```python
"""Tests de browser.py — la maquinaria de captura con un page falso (sin Playwright)."""
from pathlib import Path

import browser


class FakePage:
    """Simula lo mínimo de un Page de Playwright: screenshot + evaluate."""
    def __init__(self):
        self.scrolls = []

    def screenshot(self, path):
        Path(path).write_bytes(b"png-falso")

    def evaluate(self, script):
        self.scrolls.append(script)


def test_url_busqueda_encodea_termino_y_lang():
    url = browser.url_busqueda("Javier Milei")
    assert url.startswith("https://x.com/search?q=")
    assert "Javier%20Milei%20lang%3Aes" in url
    assert "f=live" in url


def test_capturar_pagina_saca_n_capturas_y_scrollea_entre_medio(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePage()
    rutas = browser.capturar_pagina(page, scrolls=3, esperas=(0, 0),
                                    carpeta=tmp_path / "caps", prefijo="milei")
    assert [r.name for r in rutas] == ["milei-1.png", "milei-2.png", "milei-3.png"]
    assert all(r.exists() for r in rutas)
    # Scrollea ENTRE capturas: n-1 scrolls para n capturas.
    assert len(page.scrolls) == 2
    assert "window.innerHeight * 0.9" in page.scrolls[0]


def test_capturar_pagina_un_scroll_no_scrollea(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePage()
    rutas = browser.capturar_pagina(page, scrolls=1, esperas=(0, 0),
                                    carpeta=tmp_path, prefijo="uno")
    assert len(rutas) == 1 and page.scrolls == []


def test_esperar_aleatorio_dentro_del_rango(monkeypatch):
    dormido = []
    monkeypatch.setattr(browser.time, "sleep", lambda s: dormido.append(s))
    monkeypatch.setattr(browser.random, "uniform", lambda a, b: (a + b) / 2)
    browser.esperar_aleatorio((2, 5))
    assert dormido == [3.5]


def test_sesion_invalida_es_exception():
    assert issubclass(browser.SesionInvalidaError, Exception)
```

- [ ] **Step 2: Verificar que fallan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local/test_browser.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'browser'`.

- [ ] **Step 3: Implementar `scraper_local/browser.py`**

```python
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
        navegador = p.chromium.launch(headless=headless)
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
```

- [ ] **Step 4: Verificar que pasan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add scraper_local/browser.py scraper_local/test_browser.py
git commit -m "feat(scraper): capturas de la busqueda de X por viewport+scroll, con dry-run sin login"
```

---

### Task 4: `run.py` + `config.json`

**Files:**
- Create: `scraper_local/run.py`, `scraper_local/config.json`
- Test: `scraper_local/test_run.py`

**Interfaces:**
- Consumes: `dedup.dedup_posts` (Task 1); `accounts.cargar_pool/guardar_pool/proxima_cuenta/registrar_uso/marcar_quemada/ruta_sesion` (Task 2); `browser.capturar_busqueda/SesionInvalidaError/esperar_aleatorio` (Task 3); `vision.read_capture(image, red) -> list[dict] | None` (Fase 2); del backend: `electoral._merge_bloques`, `electoral.build_evidence`, `electoral.compare_vs_pollsters`, `electoral.CANDIDATOS_DEFAULT`, `electoral.DISCLAIMER`, `electoral.CONF_MIN`, `store.write_snapshot(payload, generado_en) -> bool`.
- Produces: CLI `python run.py [--dry-run] [--config ruta]`; funciones testeables `cargar_config(ruta=None) -> dict`, `armar_bloque(posts, red, busquedas, crudos, errores) -> dict`, `armar_payload(bloque, warnings) -> dict`, `capturar_candidato(cfg, pool, candidato, carpeta, warnings) -> list[Path]`, `correr(cfg, dry_run=False) -> int`.

- [ ] **Step 1: Crear `scraper_local/config.json`**

```json
{
  "red": "twitter",
  "candidatos": null,
  "scrolls_por_candidato": 3,
  "espera_entre_scrolls": [2, 5],
  "espera_entre_candidatos": [20, 40],
  "viewport": [950, 1300],
  "headless": true,
  "min_posts_electorales": 1,
  "conservar_corridas": 3
}
```

- [ ] **Step 2: Escribir los tests (fallan)**

Crear `scraper_local/test_run.py`:

```python
"""Tests de run.py — orquestación con browser/vision/store mockeados (sin red)."""
import json
from pathlib import Path

import run
import browser as browser_mod


POST_MILEI = {"texto": "Gran discurso de Milei", "autor": "@fan", "fecha": "2 h",
              "red": "twitter", "es_electoral": True,
              "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}],
              "cita": "Gran discurso"}
POST_RUIDO = {"texto": "Partidazo de River", "autor": "@futbol", "fecha": "3 h",
              "red": "twitter", "es_electoral": False, "candidatos": [], "cita": ""}


def _cfg(**over):
    cfg = run.cargar_config(Path("no-existe.json"))
    cfg.update({"espera_entre_candidatos": [0, 0], "espera_entre_scrolls": [0, 0]})
    cfg.update(over)
    return cfg


def test_cargar_config_defaults_y_override(tmp_path):
    assert run.cargar_config(tmp_path / "nada.json")["scrolls_por_candidato"] == 3
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"scrolls_por_candidato": 7}), encoding="utf-8")
    cfg = run.cargar_config(ruta)
    assert cfg["scrolls_por_candidato"] == 7
    assert cfg["red"] == "twitter"  # el resto conserva el default


def test_armar_bloque_mapea_al_shape_de_electoral():
    bloque = run.armar_bloque([POST_MILEI, POST_RUIDO], "twitter",
                              busquedas=2, crudos=5, errores=1)
    assert bloque["network"] == "twitter"
    assert bloque["posts_by_id"]["Post_0"]["author"] == "@fan"
    assert bloque["posts_by_id"]["Post_0"]["author_url"] == "https://x.com/fan"
    assert bloque["posts_by_id"]["Post_1"]["author_url"] == "https://x.com/futbol"
    assert bloque["analysis"][0]["candidatos"][0]["nombre"] == "Javier Milei"
    assert bloque["analysis"][1]["es_electoral"] is False
    assert bloque["status"] == {"red": "twitter", "busquedas": 2, "crudos": 5,
                                "errores": 1, "encontrados": 2, "analizados": 2}


def test_armar_bloque_autor_sin_arroba_no_arma_url():
    bloque = run.armar_bloque([dict(POST_MILEI, autor="Nombre Visible")], "twitter", 1, 1, 0)
    assert bloque["posts_by_id"]["Post_0"]["author_url"] == ""


def test_armar_payload_reusa_electoral():
    bloque = run.armar_bloque([POST_MILEI, POST_RUIDO], "twitter", 2, 5, 0)
    payload = run.armar_payload(bloque, ["warn-previo"])
    assert payload["candidatos"][0]["nombre"] == "Javier Milei"
    assert payload["candidatos"][0]["por_red"] == {"twitter": 1}
    assert payload["evidencia"][0]["candidato"] == "Javier Milei"
    assert isinstance(payload["comparacion"], list)
    meta = payload["meta"]
    assert meta["total_posts"] == 2 and meta["posts_electorales"] == 1
    assert meta["bloques"] == [bloque["status"]]
    assert "termómetro" in meta["disclaimer"] or "termometro" in meta["disclaimer"].lower()
    assert "warn-previo" in meta["warnings"]


def test_capturar_candidato_rota_ante_sesion_invalida(tmp_path, monkeypatch):
    pool = {"cuentas": [
        {"alias": "muerta", "estado": "activa", "ultima_vez": "", "notas": ""},
        {"alias": "viva", "estado": "activa", "ultima_vez": "2026-09-01T00:00:00+00:00", "notas": ""},
    ]}
    usadas = []
    def fake_capturar(sesion, termino, **kw):
        usadas.append(sesion.name)
        if sesion.name == "muerta.json":
            raise browser_mod.SesionInvalidaError("challenge")
        return [tmp_path / "cap-1.png"]
    monkeypatch.setattr(run.browser, "capturar_busqueda", fake_capturar)
    warnings = []
    rutas = run.capturar_candidato(_cfg(), pool, "Javier Milei", tmp_path, warnings)
    assert usadas == ["muerta.json", "viva.json"]
    assert len(rutas) == 1
    assert pool["cuentas"][0]["estado"] == "quemada"
    assert any("quemada" in w for w in warnings)


def test_capturar_candidato_sin_cuentas_devuelve_vacio(tmp_path):
    warnings = []
    rutas = run.capturar_candidato(_cfg(), {"cuentas": []}, "X", tmp_path, warnings)
    assert rutas == [] and any("Sin cuentas activas" in w for w in warnings)


def _preparar_correr(monkeypatch, tmp_path, posts_por_captura, escritos):
    """Mockea todo lo externo de correr(): cuentas, browser, vision, store, esperas."""
    pool = {"cuentas": [{"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""}]}
    monkeypatch.setattr(run.accounts, "cargar_pool", lambda ruta=None: pool)
    monkeypatch.setattr(run.accounts, "guardar_pool", lambda p, ruta=None: None)
    monkeypatch.setattr(run, "capturar_candidato",
                        lambda cfg, pool, cand, carpeta, warnings: [tmp_path / f"{cand}.png"])
    monkeypatch.setattr(run.vision, "read_capture", lambda ruta, red: posts_por_captura)
    monkeypatch.setattr(run.store, "write_snapshot",
                        lambda payload, generado_en: escritos.append(payload) or True)
    monkeypatch.setattr(run.browser, "esperar_aleatorio", lambda rango: None)
    monkeypatch.setattr(run, "_limpiar_corridas_viejas", lambda conservar: None)
    monkeypatch.setattr(run, "DIR_CAPTURAS", tmp_path / "capturas")


def test_correr_feliz_sube_snapshot_deduplicado(monkeypatch, tmp_path):
    escritos = []
    # La misma captura para 2 candidatos -> el dedup deja 2 posts únicos.
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI, POST_RUIDO, POST_MILEI], escritos)
    rc = run.correr(_cfg(candidatos=["Javier Milei", "Axel Kicillof"]), dry_run=False)
    assert rc == 0
    assert len(escritos) == 1
    meta = escritos[0]["meta"]
    assert meta["total_posts"] == 2      # 6 crudos (3x2) -> 2 únicos
    assert meta["posts_electorales"] == 1
    assert escritos[0]["candidatos"][0]["nombre"] == "Javier Milei"


def test_correr_sin_electorales_no_pisa_snapshot(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_RUIDO], escritos)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    assert rc == 1 and escritos == []


def test_correr_dry_run_no_sube(monkeypatch, tmp_path, capsys):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=True)
    assert rc == 0 and escritos == []
    assert "posts_electorales" in capsys.readouterr().out


def test_correr_falla_subida_devuelve_1(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    monkeypatch.setattr(run.store, "write_snapshot", lambda payload, generado_en: False)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    assert rc == 1


def test_correr_lectura_fallida_cuenta_error(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, None, escritos)  # vision devuelve None (upstream)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    assert rc == 1  # sin posts electorales -> no pisa
    assert escritos == []
```

- [ ] **Step 3: Verificar que fallan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local/test_run.py`
Expected: FAIL con `ModuleNotFoundError: No module named 'run'`.

- [ ] **Step 4: Implementar `scraper_local/run.py`**

```python
"""
Orquestador del scraper local (Fase 3) — lo dispara Task Scheduler 2-3×/día.

Por candidato: capturas de la búsqueda de X (browser, rotando cuentas si se
queman) → lectura con Gemini visión (vision.read_capture, paceada por
VISION_MIN_INTERVAL) → dedup global → agregación REUSANDO la lógica del backend
(electoral._merge_bloques / build_evidence / compare_vs_pollsters) → snapshot a
Supabase (store.write_snapshot).

Regla de seguridad: si la corrida no junta `min_posts_electorales` posts
electorales, o la subida falla, NO se pisa el snapshot anterior y el exit code
es 1 (Task Scheduler lo registra como fallo).

Uso:  python run.py [--dry-run] [--config ruta]
"""
import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# El scraper corre desde el repo clonado en la PC de la oficina: reusa el código
# del backend agregándolo al path (mismo patrón que vision.py).
BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = str(BASE_DIR.parent / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import accounts  # noqa: E402
import browser  # noqa: E402
import dedup  # noqa: E402
import vision  # noqa: E402
import electoral  # noqa: E402
import store  # noqa: E402

RUTA_CONFIG = BASE_DIR / "config.json"
DIR_CAPTURAS = BASE_DIR / "capturas"
DIR_LOGS = BASE_DIR / "logs"

log = logging.getLogger("urna.scraper")

CONFIG_DEFAULT = {
    "red": "twitter",
    "candidatos": None,  # None = electoral.CANDIDATOS_DEFAULT
    "scrolls_por_candidato": 3,
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
    return cfg


def _author_url(autor: str, red: str) -> str:
    if red == "twitter" and autor.startswith("@"):
        return f"https://x.com/{autor[1:]}"
    return ""


def _slug(nombre: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in nombre.lower()).strip("-")


def armar_bloque(posts: list[dict], red: str, busquedas: int, crudos: int, errores: int) -> dict:
    """Mapea los posts de visión (ya deduplicados) al shape de bloque de electoral.
    En visión extraer y clasificar es UNA pasada, así que analizados == encontrados."""
    posts_by_id: dict[str, dict] = {}
    analysis: list[dict] = []
    for i, post in enumerate(posts):
        pid = f"Post_{i}"
        autor = post.get("autor") or ""
        posts_by_id[pid] = {
            "network": red, "author": autor, "author_url": _author_url(autor, red),
            "text": post.get("texto") or "", "post_url": "", "date": post.get("fecha") or "",
        }
        analysis.append({
            "id": pid,
            "candidatos": post.get("candidatos") or [],
            "cita": post.get("cita") or "",
            "es_electoral": bool(post.get("es_electoral", False)),
        })
    status = {"red": red, "busquedas": busquedas, "crudos": crudos, "errores": errores,
              "encontrados": len(posts), "analizados": len(posts)}
    return {"network": red, "posts_by_id": posts_by_id, "analysis": analysis, "status": status}


def armar_payload(bloque: dict, warnings: list[str]) -> dict:
    """Arma el payload del snapshot con la MISMA forma que electoral.run_boca_de_urna."""
    candidatos, baja_conf, posts_by_id, analysis = electoral._merge_bloques([bloque])
    evidencia = electoral.build_evidence(analysis, posts_by_id)
    comparacion, comp_warnings = electoral.compare_vs_pollsters(candidatos, [])
    warnings = list(warnings) + list(comp_warnings)
    if baja_conf:
        warnings.append(
            f"{baja_conf} mención(es) descartada(s) por baja confianza (< {electoral.CONF_MIN}).")
    posts_electorales = sum(1 for a in analysis if a.get("es_electoral"))
    return {
        "candidatos": candidatos, "evidencia": evidencia, "comparacion": comparacion,
        "meta": {"total_posts": bloque["status"]["encontrados"],
                 "posts_electorales": posts_electorales,
                 "analizados": bloque["status"]["analizados"],
                 "bloques": [bloque["status"]],
                 "disclaimer": electoral.DISCLAIMER, "warnings": warnings},
    }


def capturar_candidato(cfg: dict, pool: dict, candidato: str, carpeta: Path,
                       warnings: list[str]) -> list[Path]:
    """Capturas de UN candidato, rotando la cuenta UNA vez si se quema.
    Devuelve [] si no se pudo (el resto de la corrida sigue)."""
    for _intento in range(2):  # cuenta actual + una rotación
        cuenta = accounts.proxima_cuenta(pool)
        if cuenta is None:
            warnings.append(f"Sin cuentas activas: '{candidato}' quedó sin capturar.")
            return []
        try:
            rutas = browser.capturar_busqueda(
                sesion=accounts.ruta_sesion(cuenta["alias"]), termino=candidato,
                scrolls=cfg["scrolls_por_candidato"], viewport=tuple(cfg["viewport"]),
                headless=cfg["headless"], esperas=tuple(cfg["espera_entre_scrolls"]),
                carpeta=carpeta, prefijo=_slug(candidato))
            accounts.registrar_uso(pool, cuenta["alias"])
            return rutas
        except browser.SesionInvalidaError as e:
            log.warning("Cuenta '%s' quemada/challenge: %s", cuenta["alias"], e)
            accounts.marcar_quemada(pool, cuenta["alias"])
            warnings.append(f"Cuenta '{cuenta['alias']}' marcada como quemada.")
    warnings.append(f"'{candidato}' quedó sin capturar (dos cuentas fallaron).")
    return []


def _limpiar_corridas_viejas(conservar: int) -> None:
    """Borra las carpetas de capturas más viejas, conservando las últimas N."""
    if conservar <= 0 or not DIR_CAPTURAS.exists():
        return
    corridas = sorted((d for d in DIR_CAPTURAS.iterdir() if d.is_dir()), key=lambda d: d.name)
    for vieja in corridas[:-conservar]:
        shutil.rmtree(vieja, ignore_errors=True)


def correr(cfg: dict, dry_run: bool = False) -> int:
    inicio = datetime.now(timezone.utc)
    carpeta = DIR_CAPTURAS / inicio.strftime("%Y%m%d-%H%M")
    warnings: list[str] = []
    red = cfg["red"]
    candidatos = cfg.get("candidatos") or electoral.CANDIDATOS_DEFAULT

    pool = accounts.cargar_pool()
    posts_crudos: list[dict] = []
    errores = 0
    for i, candidato in enumerate(candidatos):
        log.info("Candidato %d/%d: %s", i + 1, len(candidatos), candidato)
        rutas = capturar_candidato(cfg, pool, candidato, carpeta, warnings)
        for ruta in rutas:
            leidos = vision.read_capture(ruta, red)
            if leidos is None:
                errores += 1
                warnings.append(f"Lectura fallida (Gemini) de {ruta.name}.")
                continue
            posts_crudos.extend(leidos)
        if i + 1 < len(candidatos) and rutas:
            browser.esperar_aleatorio(tuple(cfg["espera_entre_candidatos"]))
    accounts.guardar_pool(pool)

    posts = dedup.dedup_posts(posts_crudos)
    log.info("Posts: %d crudos, %d tras dedup, %d errores de lectura.",
             len(posts_crudos), len(posts), errores)
    bloque = armar_bloque(posts, red, busquedas=len(candidatos),
                          crudos=len(posts_crudos), errores=errores)
    payload = armar_payload(bloque, warnings)
    pe = payload["meta"]["posts_electorales"]

    if dry_run:
        log.info("[dry-run] NO se sube el snapshot.")
        print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
        return 0
    if pe < cfg["min_posts_electorales"]:
        log.error("Solo %d post(s) electoral(es) (mínimo %d): NO se pisa el snapshot anterior.",
                  pe, cfg["min_posts_electorales"])
        return 1
    if not store.write_snapshot(payload, inicio.isoformat()):
        log.error("La subida a Supabase falló: el snapshot anterior queda vigente.")
        return 1
    log.info("Snapshot subido: %d candidatos, %d posts electorales, %d warnings.",
             len(payload["candidatos"]), pe, len(payload["meta"]["warnings"]))
    _limpiar_corridas_viejas(cfg["conservar_corridas"])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Corrida del scraper local de la Boca de Urna.")
    ap.add_argument("--dry-run", action="store_true",
                    help="no sube el snapshot ni limpia capturas; imprime el meta")
    ap.add_argument("--config", default=None, help="ruta alternativa de config.json")
    args = ap.parse_args(argv)
    DIR_LOGS.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(
                      DIR_LOGS / f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.log",
                      encoding="utf-8")])
    cfg = cargar_config(args.config)
    return correr(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Verificar que pasan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: 37 passed.

- [ ] **Step 6: Suite del backend intacta**

Desde `backend/`: `.venv\Scripts\python.exe -m pytest -q`
Expected: 148 passed.

- [ ] **Step 7: Commit**

```bash
git add scraper_local/run.py scraper_local/config.json scraper_local/test_run.py
git commit -m "feat(scraper): orquestador run.py — capturas, vision, dedup y snapshot reusando electoral"
```

---

### Task 5: Setup — requirements, run.ps1, plantilla de cuentas, .gitignore, README

**Files:**
- Create: `scraper_local/requirements.txt`, `scraper_local/run.ps1`, `scraper_local/accounts.example.json`
- Modify: `.gitignore` (raíz del repo), `scraper_local/README.md`

**Interfaces:**
- Consumes: los CLIs de Tasks 2-4 (los comandos que el README documenta).
- Produces: setup completo de la PC de la oficina.

- [ ] **Step 1: Crear `scraper_local/requirements.txt`**

```
# Dependencias del scraper local (la PC de la oficina). El backend NO las usa.
playwright
requests
```

- [ ] **Step 2: Crear `scraper_local/run.ps1`**

```powershell
# Corrida del scraper local de la Boca de Urna (la dispara Task Scheduler).
# El exit code de run.py se propaga para que el Task Scheduler registre fallos.
Set-Location $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" run.py
exit $LASTEXITCODE
```

- [ ] **Step 3: Crear `scraper_local/accounts.example.json`**

```json
{
  "cuentas": [
    {"alias": "cuenta1", "estado": "activa", "ultima_vez": "", "notas": "creada 2026-09; mail xxx"}
  ]
}
```

- [ ] **Step 4: Agregar al `.gitignore` de la raíz**

Agregar al final (con un comentario de sección):

```
# Scraper local (PC de la oficina): estado y salidas — JAMÁS commitear
scraper_local/accounts.json
scraper_local/.sesiones/
scraper_local/capturas/
scraper_local/logs/
scraper_local/.venv/
```

- [ ] **Step 5: Actualizar `scraper_local/README.md`**

Reemplazar la sección `## Estado` por:

```markdown
## Estado
- ✅ **Fase 1:** almacén Supabase (`backend/store.py`) + modo `stored` + "Última actualización".
- ✅ **Fase 2:** `vision.py` (captura → Gemini visión → posts). Smoke test: `testdata/README.md`.
- ✅ **Fase 3:** el scraper de X (`accounts.py` + `browser.py` + `dedup.py` + `run.py`).
  Falta el tuning en la PC de la oficina (selectores/tiempos con X real).
- ⏳ Fase 4: Instagram y TikTok.
```

Y AGREGAR al final del archivo:

```markdown
## 4) Setup del scraper en la PC de la oficina (Fase 3)

Todo se corre DENTRO de `scraper_local\` (con el repo clonado):

```powershell
cd scraper_local
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

Variables de entorno (o un `.env` que cargues antes; run.py las lee del entorno):
`GEMINI_API_KEY` (y opcional `GEMINI_API_KEY_SECONDARY`), `SUPABASE_URL`,
`SUPABASE_KEY` (**service_role** — el scraper ESCRIBE), y opcional
`VISION_MIN_INTERVAL` (default 10s entre llamadas a Gemini).

**Cuentas** (descartables, login manual una sola vez por cuenta):

```powershell
.venv\Scripts\python accounts.py login cuenta1   # se abre el navegador: logueá a mano y Enter
.venv\Scripts\python accounts.py estado          # ver el pool
```

**Probar la maquinaria sin tocar X** (contra el feed falso):

```powershell
# en otra consola: python -m http.server 8123 --bind 127.0.0.1  (desde testdata\)
.venv\Scripts\python browser.py --url http://127.0.0.1:8123/feed_falso.html --scrolls 3
```

**Corrida completa** (primero en dry-run, que no sube nada):

```powershell
.venv\Scripts\python run.py --dry-run
.venv\Scripts\python run.py            # sube el snapshot a Supabase
```

**Task Scheduler** (2-3×/día): crear una tarea básica que ejecute
`powershell -ExecutionPolicy Bypass -File "<ruta>\scraper_local\run.ps1"`
en los horarios elegidos (ej. 09:00, 14:00, 19:00). Config editable en
`config.json` (scrolls, esperas, candidatos, mínimo de posts para subir).

**Qué tunear allá si algo no anda** (es lo esperable, X cambia):
- `browser.py`: `SELECTOR_FEED` (hoy `article`), `MARCAS_SESION_MUERTA`, `TIMEOUT_FEED_MS`.
- `config.json`: esperas más largas si X muestra challenges; `headless: false` para VER
  qué pasa; menos candidatos para corridas más cortas.
- Cuentas quemadas: `accounts.py estado` las muestra; reponer con `login <alias-nuevo>`.
```

- [ ] **Step 6: Verificar suites y commitear**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local` → 37 passed.

```bash
git add scraper_local/requirements.txt scraper_local/run.ps1 scraper_local/accounts.example.json .gitignore scraper_local/README.md
git commit -m "chore(scraper): setup PC oficina — requirements, run.ps1, plantilla de cuentas, gitignore y README"
```

---

### Task 6: Verificación en vivo del dry-run (la corre el orquestador)

**Files:**
- Ninguno nuevo commiteado (las capturas van a carpetas git-ignored).

**Interfaces:**
- Consumes: `browser.py` CLI dry-run (Task 3), `scraper_local/testdata/feed_falso.html` (Fase 2), instrucciones del README (Task 5).

- [ ] **Step 1: Crear el venv del scraper siguiendo el README verbatim** (valida las instrucciones): `python -m venv scraper_local/.venv`, `pip install -r requirements.txt`, `playwright install chromium`.
- [ ] **Step 2: Servir `testdata/` en `127.0.0.1:8123` y correr el dry-run**: `python browser.py --url http://127.0.0.1:8123/feed_falso.html --scrolls 3`.
- [ ] **Step 3: Verificar**: 3 PNG en `capturas/dry-run/`, la primera captura muestra el tope del feed, las siguientes muestran contenido scrolleado (mirar las imágenes). La suite completa sigue verde.
- [ ] **Step 4: (Opcional pero valioso)** Pasar una captura del dry-run por `vision.py` con la API key real para confirmar el pipeline browser→vision de punta a punta.

---

## Self-Review (hecho al escribir el plan)

1. **Spec coverage:** accounts (Task 2) ✓, browser + dry-run (Task 3) ✓, dedup (Task 1) ✓, run + config + regla de no-pisar (Task 4) ✓, requirements/run.ps1/plantilla/.gitignore/README (Task 5) ✓, verificación en vivo (Task 6) ✓. Interfaces reusadas idénticas a las del spec.
2. **Placeholders:** ninguno.
3. **Consistencia de tipos:** `capturar_busqueda(sesion, termino, scrolls, viewport, headless, esperas, carpeta, prefijo)` idéntica entre Task 3 (def) y Task 4 (call con kwargs); `SesionInvalidaError`, `esperar_aleatorio`, `dedup_posts`, API de accounts y shapes de bloque/payload consistentes entre tasks; conteo de tests acumulado: 10 (vision) + 5 + 6 + 5 + 11 = 37.
