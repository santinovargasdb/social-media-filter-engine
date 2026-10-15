# Scraper stealth + reintento in-run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bajar la tasa de challenges del scraper (stealth) y recuperar los challenges transitorios con 1 reintento in-run, sin tocar la causa-IP ni romper lo que anda.

**Architecture:** Dos cambios ortogonales en archivos existentes. `browser.py` pasa un user-agent realista (derivado de la versión real del Chromium) + locale/timezone argentinos al contexto de Playwright. `run.py` hace que `capturar_candidato` reintente una vez con la misma cuenta ante `DesafioTemporalError`, con un corte por red tras varios challenges seguidos (bloqueo de IP probable).

**Tech Stack:** Python 3 (stdlib), Playwright (sync), pytest.

## Global Constraints

- **NO rotar de cuenta ante challenge** — las dos cuentas salen por la misma IP; el reintento es con la **misma cuenta**.
- **Sin dependencias nuevas de pip.**
- **User-agent derivado de la versión real del navegador** (no hardcodear una versión); el UA resultante **no** contiene `HeadlessChrome`.
- **`locale = "es-AR"`** y **`timezone_id = "America/Argentina/Buenos_Aires"`** en el contexto.
- Parámetros como **constantes**: `ESPERA_REINTENTO = (20, 30)`, `UMBRAL_CORTE_CHALLENGES = 3`.
- **Corte por red:** al llegar a `UMBRAL_CORTE_CHALLENGES` challenges consecutivos en una red, se deja de reintentar en esa red; un éxito resetea el contador.
- **No romper tests existentes** — actualizar los que cambian de comportamiento.

---

### Task 1: Stealth en `browser.py`

**Files:**
- Modify: `scraper_local/browser.py` (nuevo helper `_user_agent`; `pagina_con_sesion` ~71-88)
- Test: `scraper_local/test_browser.py`

**Interfaces:**
- Consumes: nada.
- Produces: `browser._user_agent(version: str) -> str` — UA de Chrome para Windows usando el major de `version`; sin `HeadlessChrome`; fallback a major `"120"` si `version` no empieza con dígitos. `browser.UA_MAJOR_FALLBACK: str = "120"`.

- [ ] **Step 1: Write the failing tests**

Append to `scraper_local/test_browser.py`:

```python
def test_user_agent_usa_el_major_real_sin_headless():
    ua = browser._user_agent("140.0.7259.5")
    assert "Chrome/140.0.0.0" in ua
    assert "Windows NT 10.0" in ua
    assert "HeadlessChrome" not in ua


def test_user_agent_fallback_si_version_rara():
    ua = browser._user_agent("")
    assert "Chrome/120.0.0.0" in ua
    assert "HeadlessChrome" not in ua
    assert browser._user_agent("no-es-version").startswith("Mozilla/5.0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_browser.py -k user_agent -v`
Expected: FAIL con `AttributeError: module 'browser' has no attribute '_user_agent'`.

- [ ] **Step 3: Add the constant and helper**

In `scraper_local/browser.py`, after `TIMEOUT_FEED_MS = 30000` (line 19), add:

```python
UA_MAJOR_FALLBACK = "120"


def _user_agent(version: str) -> str:
    """UA de Chrome para Windows usando el major REAL del navegador (ej. '140.0.7259.5'
    -> 140), para que no se desactualice. Sin el token 'HeadlessChrome'. Si `version`
    no empieza con dígitos, usa UA_MAJOR_FALLBACK."""
    major = (version or "").split(".")[0]
    if not major.isdigit():
        major = UA_MAJOR_FALLBACK
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_browser.py -k user_agent -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire stealth into `pagina_con_sesion`**

In `scraper_local/browser.py`, replace the `new_context` call inside `pagina_con_sesion` (currently):

```python
        context = navegador.new_context(
            storage_state=str(sesion),
            viewport={"width": viewport[0], "height": viewport[1]},
            permissions=["notifications"])  # evita el popup "Activar notificaciones"
```

with:

```python
        context = navegador.new_context(
            storage_state=str(sesion),
            viewport={"width": viewport[0], "height": viewport[1]},
            user_agent=_user_agent(navegador.version),
            locale="es-AR",
            timezone_id="America/Argentina/Buenos_Aires",
            permissions=["notifications"])  # evita el popup "Activar notificaciones"
```

(No se puede unit-testear el contexto sin Playwright; el riesgo real —armar el UA— está cubierto por el test puro de `_user_agent`. Este paso es transcripción.)

- [ ] **Step 6: Run the browser test suite**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_browser.py -v`
Expected: PASS (los 2 nuevos + los existentes).

- [ ] **Step 7: Commit**

```bash
git add scraper_local/browser.py scraper_local/test_browser.py
git commit -m "feat(urna): stealth en el scraper (user-agent real, locale es-AR, timezone AR)"
```

(Agregá al final del mensaje el trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

### Task 2: Reintento + corte por red en `run.py`

**Files:**
- Modify: `scraper_local/run.py` (constantes nuevas; `capturar_candidato` ~210-246; `correr` ~268-273)
- Test: `scraper_local/test_run.py`

**Interfaces:**
- Consumes: `browser.DesafioTemporalError`, `browser.SesionInvalidaError`, `browser.esperar_aleatorio` (ya existen).
- Produces:
  - `run.ESPERA_REINTENTO: tuple = (20, 30)`, `run.UMBRAL_CORTE_CHALLENGES: int = 3`.
  - `run.capturar_candidato(red_nombre, cfg, pool, candidato, carpeta, warnings, estado_red=None)` — nuevo parámetro opcional `estado_red: dict | None`. Si es `None`, usa uno propio. `estado_red` lleva `{"challenges_consecutivos": int}`; se incrementa al saltear un candidato por challenge y se resetea a 0 en un éxito.

- [ ] **Step 1: Write the failing tests**

In `scraper_local/test_run.py`, (a) **replace** the existing `test_capturar_candidato_challenge_no_quema_ni_rota` (currently ~line 148) with the version below, and (b) **append** the three new tests:

```python
def test_capturar_candidato_challenge_reintenta_sin_quemar_ni_rotar(tmp_path, monkeypatch):
    """DesafioTemporalError: reintenta UNA vez con la MISMA cuenta (el challenge es a la
    IP, no a la cuenta). Si persiste: warning + salteado, sin quemar ni rotar a la otra."""
    monkeypatch.setattr(run.browser, "esperar_aleatorio", lambda rango: None)
    pool = {"cuentas": [
        {"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""},
        {"alias": "b", "estado": "activa", "ultima_vez": "2026-09-01T00:00:00+00:00", "notas": ""},
    ]}
    usadas = []
    def challenge(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings):
        usadas.append(sesion.name)
        raise browser_mod.DesafioTemporalError("captcha en pantalla")
    monkeypatch.setattr(twitter, "capturar", challenge)
    warnings = []
    capturas = run.capturar_candidato("twitter", _cfg(), pool, "Javier Milei", tmp_path, warnings)
    assert capturas == []
    assert usadas == ["a.json", "a.json"]  # reintentó la MISMA cuenta, NO rotó a b
    assert all(c["estado"] == "activa" for c in pool["cuentas"])  # nadie quemada
    assert any("challenge" in w for w in warnings)


def test_capturar_candidato_reintenta_y_recupera(tmp_path, monkeypatch):
    monkeypatch.setattr(run.browser, "esperar_aleatorio", lambda rango: None)
    pool = {"cuentas": [{"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""}]}
    llamadas = []
    def challenge_luego_ok(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings):
        llamadas.append(sesion.name)
        if len(llamadas) == 1:
            raise browser_mod.DesafioTemporalError("feed lento")
        return [{"ruta": tmp_path / "cap-1.png", "contexto": ""}]
    monkeypatch.setattr(twitter, "capturar", challenge_luego_ok)
    estado = {"challenges_consecutivos": 0}
    capturas = run.capturar_candidato("twitter", _cfg(), pool, "Milei", tmp_path, [], estado)
    assert len(capturas) == 1
    assert llamadas == ["a.json", "a.json"]        # reintentó la MISMA cuenta
    assert estado["challenges_consecutivos"] == 0  # el éxito resetea el contador


def test_capturar_candidato_challenge_persistente_incrementa_contador(tmp_path, monkeypatch):
    monkeypatch.setattr(run.browser, "esperar_aleatorio", lambda rango: None)
    pool = {"cuentas": [{"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""}]}
    llamadas = []
    def challenge(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings):
        llamadas.append(sesion.name)
        raise browser_mod.DesafioTemporalError("captcha")
    monkeypatch.setattr(twitter, "capturar", challenge)
    estado = {"challenges_consecutivos": 0}
    capturas = run.capturar_candidato("twitter", _cfg(), pool, "Milei", tmp_path, [], estado)
    assert capturas == []
    assert llamadas == ["a.json", "a.json"]        # 1er intento + 1 reintento
    assert estado["challenges_consecutivos"] == 1
    assert pool["cuentas"][0]["estado"] == "activa"  # NO quemada


def test_capturar_candidato_corte_no_reintenta(tmp_path, monkeypatch):
    monkeypatch.setattr(run.browser, "esperar_aleatorio", lambda rango: None)
    pool = {"cuentas": [{"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""}]}
    llamadas = []
    def challenge(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings):
        llamadas.append(sesion.name)
        raise browser_mod.DesafioTemporalError("captcha")
    monkeypatch.setattr(twitter, "capturar", challenge)
    estado = {"challenges_consecutivos": 3}  # corte ya activo (bloqueo de IP probable)
    capturas = run.capturar_candidato("twitter", _cfg(), pool, "Milei", tmp_path, [], estado)
    assert capturas == []
    assert llamadas == ["a.json"]  # sin reintento: una sola llamada
```

- [ ] **Step 2: Run the new/updated tests to verify they fail**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_run.py -k "challenge or reintenta or corte" -v`
Expected: FAIL — `test_capturar_candidato_reintenta_y_recupera` da `TypeError` (capturar_candidato aún no acepta `estado_red`) y los de challenge fallan por el conteo de llamadas (hoy no reintenta).

- [ ] **Step 3: Add the constants**

In `scraper_local/run.py`, after `DIR_RESULTADOS = DIR_LOGS / "resultados"` (~line 32), add:

```python
ESPERA_REINTENTO = (20, 30)        # seg: espera antes de reintentar la misma cuenta ante challenge
UMBRAL_CORTE_CHALLENGES = 3        # challenges consecutivos por red antes de dejar de reintentar
```

- [ ] **Step 4: Rewrite `capturar_candidato` with retry + circuit breaker**

In `scraper_local/run.py`, replace the whole `capturar_candidato` function with:

```python
def capturar_candidato(red_nombre: str, cfg: dict, pool: dict, candidato: str,
                       carpeta: Path, warnings: list[str],
                       estado_red: dict | None = None) -> list[dict]:
    """Capturas de UN candidato en UNA red, rotando la cuenta UNA vez si se quema.
    Ante challenge (DesafioTemporalError) reintenta UNA vez con la MISMA cuenta tras una
    espera: el challenge es a la IP, no a la cuenta; rotar chocaría el mismo muro. El
    redirect a login (SesionInvalidaError) sí quema y rota. `estado_red` lleva los
    challenges consecutivos de la red en la corrida: al llegar a UMBRAL_CORTE_CHALLENGES
    se asume bloqueo de IP y se saltea rápido, sin reintentar. Devuelve [] si no se pudo."""
    red_mod = redes.POR_NOMBRE[red_nombre]
    if estado_red is None:
        estado_red = {"challenges_consecutivos": 0}
    estado_red.setdefault("challenges_consecutivos", 0)
    for _intento in range(2):  # cuenta actual + una rotación ante quema
        cuenta = accounts.proxima_cuenta(pool)
        if cuenta is None:
            warnings.append(f"[{red_nombre}] Sin cuentas activas: '{candidato}' quedó sin capturar.")
            return []
        reintentar = estado_red["challenges_consecutivos"] < UMBRAL_CORTE_CHALLENGES
        for _reintento in range(2 if reintentar else 1):  # 1er intento (+1 reintento si corresponde)
            try:
                capturas = red_mod.capturar(
                    sesion=accounts.ruta_sesion(cuenta["alias"], red_nombre), termino=candidato,
                    cfg=cfg, cfg_red=cfg["redes"][red_nombre], carpeta=carpeta,
                    prefijo=f"{red_nombre}-{_slug(candidato)}", warnings=warnings)
                accounts.registrar_uso(pool, cuenta["alias"])
                estado_red["challenges_consecutivos"] = 0  # éxito: resetea el corte
                return capturas
            except browser.DesafioTemporalError as e:
                if _reintento == 0 and reintentar:
                    log.warning("[%s] Challenge con '%s' (%s); reintento con la misma cuenta "
                                "en %d-%ds.", red_nombre, candidato, e, *ESPERA_REINTENTO)
                    browser.esperar_aleatorio(ESPERA_REINTENTO)
                    continue  # reintenta la MISMA cuenta
                estado_red["challenges_consecutivos"] += 1
                log.warning("[%s] Challenge temporal con '%s': %s (se saltea; la cuenta sigue activa).",
                            red_nombre, candidato, e)
                warnings.append(f"[{red_nombre}] '{candidato}' salteado por challenge temporal "
                                f"({e}); la cuenta sigue activa.")
                return []
            except browser.SesionInvalidaError as e:
                log.warning("[%s] Cuenta '%s' quemada/challenge: %s", red_nombre, cuenta["alias"], e)
                accounts.marcar_quemada(pool, cuenta["alias"])
                warnings.append(f"[{red_nombre}] Cuenta '{cuenta['alias']}' marcada como quemada.")
                break  # rompe el reintento → el loop externo rota de cuenta
            except Exception as e:
                log.error("[%s] Error inesperado capturando '%s': %s", red_nombre, candidato, e)
                warnings.append(f"[{red_nombre}] '{candidato}' quedó sin capturar (error del navegador: {e}).")
                return []
    warnings.append(f"[{red_nombre}] '{candidato}' quedó sin capturar (dos cuentas fallaron).")
    return []
```

- [ ] **Step 5: Wire `estado_red` into `correr`**

In `scraper_local/run.py`, inside `correr`, in the per-red loop — replace:

```python
        pool = accounts.cargar_pool(red=red_nombre)
        crudos_red: list[dict] = []
        errores = 0
        for i, candidato in enumerate(candidatos):
            log.info("[%s] Candidato %d/%d: %s", red_nombre, i + 1, len(candidatos), candidato)
            capturas = capturar_candidato(red_nombre, cfg, pool, candidato, carpeta, warnings)
```

with:

```python
        pool = accounts.cargar_pool(red=red_nombre)
        crudos_red: list[dict] = []
        errores = 0
        estado_red = {"challenges_consecutivos": 0}
        for i, candidato in enumerate(candidatos):
            log.info("[%s] Candidato %d/%d: %s", red_nombre, i + 1, len(candidatos), candidato)
            capturas = capturar_candidato(red_nombre, cfg, pool, candidato, carpeta, warnings, estado_red)
```

- [ ] **Step 6: Update the `_preparar_correr` mock signature**

In `scraper_local/test_run.py`, in `_preparar_correr`, replace the `capturar_candidato` monkeypatch:

```python
    monkeypatch.setattr(run, "capturar_candidato",
                        lambda red_nombre, cfg, pool, cand, carpeta, warnings: [
                            {"ruta": tmp_path / f"{red_nombre}-{cand}.png", "contexto": ""}])
```

with (acepta el nuevo parámetro `estado_red`):

```python
    monkeypatch.setattr(run, "capturar_candidato",
                        lambda red_nombre, cfg, pool, cand, carpeta, warnings, estado_red=None: [
                            {"ruta": tmp_path / f"{red_nombre}-{cand}.png", "contexto": ""}])
```

- [ ] **Step 7: Run the whole scraper test suite**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_run.py test_browser.py test_alertas.py -v`
Expected: PASS — los 4 tests nuevos/actualizados de challenge + todos los existentes (incluido `test_capturar_candidato_rota_ante_sesion_invalida`, que sigue rotando ante `SesionInvalidaError`).

- [ ] **Step 8: Commit**

```bash
git add scraper_local/run.py scraper_local/test_run.py
git commit -m "feat(urna): reintento in-run ante challenge + corte por red (bloqueo de IP)"
```

(Agregá al final del mensaje el trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

## Self-Review

**1. Spec coverage:**
- Stealth (UA real sin HeadlessChrome + locale es-AR + timezone AR) → Task 1. ✓
- Reintento 1× misma cuenta ante challenge + espera → Task 2 (Step 4). ✓
- Corte por red a `UMBRAL_CORTE_CHALLENGES`, reset en éxito → Task 2 (Step 4, `estado_red`). ✓
- No rotar de cuenta ante challenge; sí quemar+rotar ante SesionInvalida → preservado en Step 4. ✓
- Parámetros como constantes → Task 2 (Step 3). ✓
- Actualizar tests que cambian + lambda de `_preparar_correr` → Task 2 (Steps 1, 6). ✓
- Sin dependencias nuevas → ambas tasks. ✓

**2. Placeholder scan:** Sin TBD/TODO. `{major}` es interpolación real; `"120"` es el fallback documentado. Todos los pasos de código muestran el código.

**3. Type consistency:** `_user_agent(version: str) -> str` usado igual en Task 1. `capturar_candidato(..., estado_red=None)` coincide entre la definición (Step 4), la llamada de `correr` (Step 5) y los tests (Steps 1, 6). `estado_red["challenges_consecutivos"]` usado consistentemente. Constantes `ESPERA_REINTENTO`/`UMBRAL_CORTE_CHALLENGES` definidas en Step 3 y usadas en Step 4.
