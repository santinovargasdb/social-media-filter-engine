# Aviso matutino de corridas fallidas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que a las 9 AM aparezca un cartel a pantalla completa en la PC de la oficina resumiendo cualquier corrida del scraper que haya salido degradada o fallida desde la última vez que se miró.

**Architecture:** Python **decide** y deja el veredicto escrito (`scraper_local/logs/resultados/<runid>.json`) al terminar cada corrida; un script de PowerShell disparado por una tarea de las 9h **lee** esos resultados y **muestra** una ventana nativa de Windows. La lógica de "qué es una alerta" vive en un módulo puro y testeable (`alertas.py`).

**Tech Stack:** Python 3 (stdlib: `json`, `pathlib`, `datetime`), pytest, PowerShell 5.1 + WinForms (`System.Windows.Forms` / `System.Drawing`), Task Scheduler.

## Global Constraints

- **Sin dependencias nuevas de pip.** La ventana es WinForms nativo; Python sólo escribe un JSON con stdlib.
- **Todo en UTC.** `fin_utc` y `.ultimo-visto` se guardan en ISO-UTC; las comparaciones de tiempo se hacen en UTC. La PC está en UTC-3.
- **Umbral de avisos = 8 por defecto**, configurable en `config.json` → `"alertas": { "umbral_warnings": 8 }`.
- **`exit_code == 2` (lock / ya había otra corrida) NO alerta.** Es normal al solaparse.
- **Sólo se escribe resultado en corridas reales** (no en `--dry-run`).
- **Título fijo del cartel:** `⚠️ Boca de Urna: corrida con problemas`.
- **Archivos que lee PowerShell en UTF-8** (`Set-Content -Encoding utf8`).
- **La tarea de las 9h debe ser LogonType Interactive** (usuario logueado): la ventana necesita un escritorio real.
- Nada de esto debe romper la corrida: escribir el resultado se envuelve en `try/except` que loguea y sigue.

---

### Task 1: `alertas.py` — lógica pura del veredicto

**Files:**
- Create: `scraper_local/alertas.py`
- Test: `scraper_local/test_alertas.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `alertas.TITULO: str` — `"⚠️ Boca de Urna: corrida con problemas"`.
  - `alertas.evaluar(resultado: dict, umbral_warnings: int = 8) -> dict | None` — devuelve `{"nivel": "alerta", "titulo": str, "mensaje": str}` si hay que avisar, o `None` si la corrida estuvo bien. `resultado` tiene las claves: `exit_code: int`, `posts_por_red: dict[str,int]`, `cuentas_quemadas: list[str]`, `warnings_total: int`.

- [ ] **Step 1: Write the failing test**

Create `scraper_local/test_alertas.py`:

```python
"""Tests de alertas.evaluar — lógica pura, sin I/O."""
import alertas


def _base(**over):
    r = {"exit_code": 0,
         "posts_por_red": {"twitter": 100, "tiktok": 300, "instagram": 40},
         "warnings_total": 2,
         "cuentas_quemadas": []}
    r.update(over)
    return r


def test_corrida_sana_no_alerta():
    assert alertas.evaluar(_base()) is None


def test_red_en_cero_alerta():
    v = alertas.evaluar(_base(posts_por_red={"twitter": 79, "tiktok": 0, "instagram": 38}))
    assert v is not None and v["nivel"] == "alerta"
    assert "tiktok" in v["mensaje"]
    assert v["titulo"] == alertas.TITULO


def test_muchos_avisos_alerta():
    assert alertas.evaluar(_base(warnings_total=23)) is not None


def test_exit_code_1_alerta():
    assert alertas.evaluar(_base(exit_code=1)) is not None


def test_lock_exit_code_2_no_alerta():
    assert alertas.evaluar(_base(exit_code=2)) is None


def test_cuenta_quemada_alerta():
    v = alertas.evaluar(_base(cuentas_quemadas=["[tiktok] Cuenta 'tt1' marcada como quemada."]))
    assert v is not None


def test_umbral_configurable():
    assert alertas.evaluar(_base(warnings_total=5), umbral_warnings=4) is not None
    assert alertas.evaluar(_base(warnings_total=5), umbral_warnings=8) is None


def test_solo_twitter_con_posts_no_alerta():
    assert alertas.evaluar(_base(posts_por_red={"twitter": 119})) is None


def test_solo_twitter_en_cero_alerta():
    assert alertas.evaluar(_base(posts_por_red={"twitter": 0})) is not None


def test_varios_motivos_se_concatenan():
    v = alertas.evaluar(_base(exit_code=1, warnings_total=30,
                              posts_por_red={"twitter": 0}))
    assert v["mensaje"].count(".") >= 2  # al menos tres motivos, uno por frase
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_alertas.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'alertas'`.

- [ ] **Step 3: Write minimal implementation**

Create `scraper_local/alertas.py`:

```python
"""Decide si una corrida del scraper merece un aviso. Lógica pura, sin I/O ni UI.

El resultado lo arma run.py y lo consume aviso-matutino.ps1; acá sólo se aplica
la regla de "cuándo es alerta".
"""

TITULO = "⚠️ Boca de Urna: corrida con problemas"


def evaluar(resultado: dict, umbral_warnings: int = 8) -> dict | None:
    """Devuelve {'nivel','titulo','mensaje'} si la corrida merece alerta, o None.

    Es alerta si se cumple CUALQUIERA:
      1. exit_code no es 0 ni 2  (crash / pocos posts / subida fallida; 2 = lock, normal)
      2. alguna red corrida trajo 0 posts
      3. hubo al menos una cuenta quemada
      4. los avisos totales llegan al umbral
    """
    motivos: list[str] = []

    if resultado.get("exit_code") not in (0, 2):
        motivos.append("No se pudo actualizar el tablero (la corrida falló).")

    ceros = [red for red, n in (resultado.get("posts_por_red") or {}).items() if n == 0]
    if ceros:
        motivos.append(f"Sin posts en: {', '.join(ceros)} (captcha / cuenta quemada).")

    quemadas = resultado.get("cuentas_quemadas") or []
    if quemadas:
        motivos.append(f"{len(quemadas)} cuenta(s) quemada(s) en esta corrida.")

    if (resultado.get("warnings_total") or 0) >= umbral_warnings:
        motivos.append(f"{resultado['warnings_total']} avisos en la corrida.")

    if not motivos:
        return None
    return {"nivel": "alerta", "titulo": TITULO, "mensaje": " ".join(motivos)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_alertas.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add scraper_local/alertas.py scraper_local/test_alertas.py
git commit -m "feat(urna): alertas.evaluar decide si una corrida merece aviso"
```

---

### Task 2: `run.py` escribe el resultado de cada corrida

**Files:**
- Modify: `scraper_local/run.py` (imports ~33-44; `correr()` 257-308; `main()` 311-334)
- Test: `scraper_local/test_run.py`

**Interfaces:**
- Consumes: `alertas.evaluar`, `alertas.TITULO` (Task 1).
- Produces:
  - Módulo-nivel `run.DIR_RESULTADOS: Path` = `DIR_LOGS / "resultados"`.
  - `run._armar_resultado(runid, redes_corridas, exit_code, posts_por_red, warnings_finales, posts_electorales, umbral_warnings) -> dict` — arma el dict de resultado (incluye `runid`, `fin_utc`, `redes`, `exit_code`, `posts_por_red`, `warnings_total`, `cuentas_quemadas`, `posts_electorales`, `nivel`, `titulo`, `mensaje`). `cuentas_quemadas` = los `warnings_finales` que contienen `"marcada como quemada"`.
  - `run._escribir_resultado(resultado: dict) -> None` — escribe `DIR_RESULTADOS/<runid>.json` (UTF-8) y poda a las últimas 60; nunca lanza.
  - Efecto: cada corrida real deja un `logs/resultados/<runid>.json`.

- [ ] **Step 1: Write the failing tests**

Append to `scraper_local/test_run.py`:

```python
def test_armar_resultado_sano_es_ok():
    r = run._armar_resultado("20261013-1903", ["twitter", "tiktok", "instagram"],
                             exit_code=0, posts_por_red={"twitter": 116, "tiktok": 319,
                             "instagram": 47}, warnings_finales=["x", "y"],
                             posts_electorales=332, umbral_warnings=8)
    assert r["nivel"] == "ok" and r["mensaje"] == "" and r["runid"] == "20261013-1903"
    assert r["warnings_total"] == 2 and r["cuentas_quemadas"] == []
    assert r["fin_utc"].endswith("+00:00")


def test_armar_resultado_tiktok_cero_es_alerta():
    r = run._armar_resultado("20261007-1900", ["twitter", "tiktok", "instagram"],
                             exit_code=0, posts_por_red={"twitter": 79, "tiktok": 0,
                             "instagram": 38}, warnings_finales=["a"] * 19,
                             posts_electorales=78, umbral_warnings=8)
    assert r["nivel"] == "alerta" and "tiktok" in r["mensaje"]


def test_armar_resultado_detecta_cuenta_quemada():
    r = run._armar_resultado("20261005-1900", ["twitter"], exit_code=0,
                             posts_por_red={"twitter": 90},
                             warnings_finales=["[twitter] Cuenta 'cuenta1' marcada como quemada."],
                             posts_electorales=90, umbral_warnings=8)
    assert r["cuentas_quemadas"] == ["[twitter] Cuenta 'cuenta1' marcada como quemada."]
    assert r["nivel"] == "alerta"


def test_escribir_resultado_crea_json_y_poda(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "DIR_RESULTADOS", tmp_path / "resultados")
    for i in range(63):
        run._escribir_resultado({"runid": f"202610{i:02d}-1200", "nivel": "ok"})
    quedan = sorted((tmp_path / "resultados").glob("*.json"))
    assert len(quedan) == 60  # podó las 3 más viejas
    assert (tmp_path / "resultados" / "20261062-1200.json").exists()


def test_correr_feliz_escribe_resultado_ok(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    monkeypatch.setattr(run, "DIR_RESULTADOS", tmp_path / "resultados")
    run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    jsons = list((tmp_path / "resultados").glob("*.json"))
    assert len(jsons) == 1
    data = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert data["exit_code"] == 0 and data["nivel"] == "ok"
    assert data["posts_por_red"]["twitter"] >= 1


def test_correr_dry_run_no_escribe_resultado(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    monkeypatch.setattr(run, "DIR_RESULTADOS", tmp_path / "resultados")
    run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=True)
    assert not (tmp_path / "resultados").exists()


def test_correr_falla_subida_escribe_alerta(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    monkeypatch.setattr(run, "DIR_RESULTADOS", tmp_path / "resultados")
    monkeypatch.setattr(run.store, "write_snapshot", lambda payload, generado_en: False)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    assert rc == 1
    data = json.loads(next((tmp_path / "resultados").glob("*.json")).read_text(encoding="utf-8"))
    assert data["exit_code"] == 1 and data["nivel"] == "alerta"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_run.py -k "resultado" -v`
Expected: FAIL con `AttributeError: module 'run' has no attribute '_armar_resultado'` (y similares).

- [ ] **Step 3: Add the import and constants**

In `scraper_local/run.py`, add `alertas` to the local-module imports (after line 39, `import store`):

```python
import store  # noqa: E402
import alertas  # noqa: E402
```

And add the results dir next to `DIR_LOGS` (after line 43):

```python
DIR_LOGS = BASE_DIR / "logs"
DIR_RESULTADOS = DIR_LOGS / "resultados"
```

- [ ] **Step 4: Add the helper functions**

In `scraper_local/run.py`, add these just above `def correr(` (line 257):

```python
def _armar_resultado(runid, redes_corridas, exit_code, posts_por_red,
                     warnings_finales, posts_electorales, umbral_warnings):
    """Arma el dict de resultado de una corrida y le pega el veredicto de alertas."""
    cuentas_quemadas = [w for w in warnings_finales if "marcada como quemada" in w]
    resultado = {
        "runid": runid,
        "fin_utc": datetime.now(timezone.utc).isoformat(),
        "redes": list(redes_corridas),
        "exit_code": exit_code,
        "posts_por_red": dict(posts_por_red),
        "warnings_total": len(warnings_finales),
        "cuentas_quemadas": cuentas_quemadas,
        "posts_electorales": posts_electorales,
    }
    veredicto = alertas.evaluar(resultado, umbral_warnings=umbral_warnings)
    resultado["nivel"] = veredicto["nivel"] if veredicto else "ok"
    resultado["titulo"] = veredicto["titulo"] if veredicto else ""
    resultado["mensaje"] = veredicto["mensaje"] if veredicto else ""
    return resultado


def _podar_resultados(conservar: int) -> None:
    """Deja sólo los últimos N json de resultados (son chicos; evita acumular)."""
    if conservar <= 0 or not DIR_RESULTADOS.exists():
        return
    archivos = sorted(DIR_RESULTADOS.glob("*.json"), key=lambda p: p.name)
    for viejo in archivos[:-conservar]:
        viejo.unlink(missing_ok=True)


def _escribir_resultado(resultado: dict) -> None:
    """Escribe logs/resultados/<runid>.json. Nunca rompe la corrida."""
    try:
        DIR_RESULTADOS.mkdir(parents=True, exist_ok=True)
        ruta = DIR_RESULTADOS / f"{resultado['runid']}.json"
        ruta.write_text(json.dumps(resultado, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        _podar_resultados(60)
    except Exception:
        log.exception("No se pudo escribir el resultado de la corrida (se ignora).")
```

- [ ] **Step 5: Track `posts_por_red` inside the network loop of `correr()`**

In `correr()`, declare the accumulator next to `bloques` (line 263) and fill it after the dedup log line (line 287). Replace:

```python
    bloques: list[dict] = []
    for red_nombre in cfg["redes"]:
```

with:

```python
    bloques: list[dict] = []
    posts_por_red: dict[str, int] = {}
    for red_nombre in cfg["redes"]:
```

and replace:

```python
        posts_red = dedup.dedup_posts(crudos_red)
        log.info("[%s] Posts: %d crudos, %d tras dedup, %d errores de lectura.",
                 red_nombre, len(crudos_red), len(posts_red), errores)
```

with:

```python
        posts_red = dedup.dedup_posts(crudos_red)
        posts_por_red[red_nombre] = len(posts_red)
        log.info("[%s] Posts: %d crudos, %d tras dedup, %d errores de lectura.",
                 red_nombre, len(crudos_red), len(posts_red), errores)
```

- [ ] **Step 6: Write the result on every real exit of `correr()`**

Replace the tail of `correr()` (lines 291-308, from `payload = armar_payload(...)` to `return 0`) with:

```python
    payload = armar_payload(bloques, warnings)
    pe = payload["meta"]["posts_electorales"]
    warnings_finales = payload["meta"]["warnings"]

    if dry_run:
        log.info("[dry-run] NO se sube el snapshot.")
        print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
        return 0

    exit_code = 0
    if pe < cfg["min_posts_electorales"]:
        log.error("Solo %d post(s) electoral(es) (mínimo %d): NO se pisa el snapshot anterior.",
                  pe, cfg["min_posts_electorales"])
        exit_code = 1
    elif not store.write_snapshot(payload, inicio.isoformat()):
        log.error("La subida a Supabase falló: el snapshot anterior queda vigente.")
        exit_code = 1
    else:
        log.info("Snapshot subido: %d candidatos, %d posts electorales, %d warnings.",
                 len(payload["candidatos"]), pe, len(warnings_finales))
        _limpiar_corridas_viejas(cfg["conservar_corridas"])

    umbral = (cfg.get("alertas") or {}).get("umbral_warnings", 8)
    resultado = _armar_resultado(inicio.strftime("%Y%m%d-%H%M"), cfg["redes"].keys(),
                                 exit_code, posts_por_red, warnings_finales, pe, umbral)
    _escribir_resultado(resultado)
    return exit_code
```

- [ ] **Step 7: Catch crashes in `main()` so even a failure leaves a result**

Replace the `try/except` block of `main()` (lines 328-334) with:

```python
    try:
        with lock_de_instancia(LOCK_PATH):
            return correr(cfg, dry_run=args.dry_run)
    except InstanciaActivaError as e:
        log.error("No arranco: %s. Ya hay una corrida en curso; evito colisionar "
                  "por las cuentas y la RAM.", e)
        return 2
    except Exception:
        log.exception("La corrida crasheó sin terminar.")
        if not args.dry_run:
            runid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
            _escribir_resultado({
                "runid": runid,
                "fin_utc": datetime.now(timezone.utc).isoformat(),
                "redes": list(cfg.get("redes", {})),
                "exit_code": 1, "posts_por_red": {}, "warnings_total": 0,
                "cuentas_quemadas": [], "posts_electorales": 0,
                "nivel": "alerta", "titulo": alertas.TITULO,
                "mensaje": "La corrida crasheó, revisá el log.",
            })
        return 1
```

- [ ] **Step 8: Run the whole scraper test suite**

Run: `cd scraper_local && .venv/Scripts/python -m pytest test_run.py test_alertas.py -v`
Expected: PASS (los nuevos tests de `resultado` + todos los existentes siguen verdes).

- [ ] **Step 9: Commit**

```bash
git add scraper_local/run.py scraper_local/test_run.py
git commit -m "feat(urna): cada corrida escribe logs/resultados/<runid>.json con su veredicto"
```

---

### Task 3: `aviso-matutino.ps1` — cartel full-screen a las 9h

**Files:**
- Create: `scraper_local/aviso-matutino.ps1`

**Interfaces:**
- Consumes: los `scraper_local/logs/resultados/*.json` que escribe Task 2 (claves `fin_utc`, `nivel`, `mensaje`).
- Produces: el marcador `scraper_local/logs/resultados/.ultimo-visto` (ISO-UTC de la última vez que se cerró el cartel). Acepta el switch `-Simular`.

- [ ] **Step 1: Write the script**

Create `scraper_local/aviso-matutino.ps1`:

```powershell
param([switch]$Simular)
# Cartel a pantalla completa (9h, Task Scheduler) que resume corridas con problemas
# desde la ultima vez que se miro. Lee logs\resultados\*.json (los escribe run.py).
# -Simular: muestra un cartel de ejemplo sin tocar el marcador (verificacion visual).
$ErrorActionPreference = 'Stop'
$dirResultados = Join-Path $PSScriptRoot "logs\resultados"
$marcador = Join-Path $dirResultados ".ultimo-visto"

function Show-Cartel {
    param([string[]]$Lineas)
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.FormBorderStyle = 'None'
    $form.WindowState = 'Maximized'
    $form.TopMost = $true
    $form.KeyPreview = $true
    $form.BackColor = [System.Drawing.Color]::FromArgb(140, 20, 20)

    $titulo = New-Object System.Windows.Forms.Label
    $titulo.Text = "Boca de Urna: corrida con problemas"
    $titulo.ForeColor = [System.Drawing.Color]::White
    $titulo.Font = New-Object System.Drawing.Font("Segoe UI", 34, [System.Drawing.FontStyle]::Bold)
    $titulo.TextAlign = 'MiddleCenter'
    $titulo.Dock = 'Top'
    $titulo.Height = 170

    $cuerpo = New-Object System.Windows.Forms.Label
    $cuerpo.Text = ($Lineas -join "`r`n`r`n")
    $cuerpo.ForeColor = [System.Drawing.Color]::White
    $cuerpo.Font = New-Object System.Drawing.Font("Segoe UI", 20)
    $cuerpo.TextAlign = 'MiddleCenter'
    $cuerpo.Dock = 'Fill'

    $boton = New-Object System.Windows.Forms.Button
    $boton.Text = "Entendido"
    $boton.Font = New-Object System.Drawing.Font("Segoe UI", 20, [System.Drawing.FontStyle]::Bold)
    $boton.ForeColor = [System.Drawing.Color]::FromArgb(140, 20, 20)
    $boton.BackColor = [System.Drawing.Color]::White
    $boton.Dock = 'Bottom'
    $boton.Height = 110
    $boton.Add_Click({ $form.Close() })

    $form.Add_KeyDown({ if ($_.KeyCode -eq 'Escape') { $form.Close() } })
    $form.Controls.Add($cuerpo)
    $form.Controls.Add($titulo)
    $form.Controls.Add($boton)
    $form.Add_Shown({ $form.Activate() })
    [void]$form.ShowDialog()
}

if ($Simular) {
    Show-Cartel -Lineas @("13/10 16h - Sin posts en: tiktok (captcha / cuenta quemada). 19 avisos en la corrida.")
    return
}

$ultimoVisto = [datetime]::new(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
if (Test-Path $marcador) {
    try { $ultimoVisto = ([datetime]((Get-Content $marcador -Raw).Trim())).ToUniversalTime() } catch {}
}

$ahora = (Get-Date).ToUniversalTime()
$lineas = New-Object System.Collections.Generic.List[string]
$huboCorridaReciente = $false

if (Test-Path $dirResultados) {
    foreach ($a in (Get-ChildItem -Path $dirResultados -Filter *.json -ErrorAction SilentlyContinue)) {
        try { $r = Get-Content $a.FullName -Raw | ConvertFrom-Json } catch { continue }
        if (-not $r.fin_utc) { continue }
        $fin = ([datetime]$r.fin_utc).ToUniversalTime()
        if ($fin -gt $ahora.AddHours(-20)) { $huboCorridaReciente = $true }
        if ($r.nivel -eq 'alerta' -and $fin -gt $ultimoVisto) {
            $cuando = $fin.ToLocalTime().ToString("dd/MM HH'h'")
            $lineas.Add("$cuando - $($r.mensaje)")
        }
    }
}

if (-not $huboCorridaReciente) {
    $lineas.Add("No hubo ninguna corrida registrada en las ultimas 20 h (PC apagada o la tarea no disparo).")
}

if ($lineas.Count -eq 0) { return }   # todo bien: no molestar

Show-Cartel -Lineas $lineas
# Marcar "visto" SOLO despues de cerrar el cartel (si se mata el script, reaparece)
$ahora.ToString("o") | Set-Content -Path $marcador -Encoding utf8
```

- [ ] **Step 2: Verify the window renders (`-Simular`)**

Run: `powershell -NoProfile -File scraper_local/aviso-matutino.ps1 -Simular`
Expected: aparece una ventana roja a pantalla completa con el título, una línea de ejemplo y el botón "Entendido". Cerrarla con el botón **y** volver a abrir para cerrar con **Escape**. El archivo `.ultimo-visto` NO debe crearse (modo simulación).

Run: `test -f scraper_local/logs/resultados/.ultimo-visto && echo EXISTE || echo "no existe (correcto)"`
Expected: `no existe (correcto)`.

- [ ] **Step 3: End-to-end — una alerta pendiente aparece una sola vez**

Crear un resultado "alerta" a mano (con `fin_utc` **reciente pero pasado**, dentro de las últimas 20 h para que no dispare también el "no corrió") y borrar el marcador. Reemplazá `<HOY>` por la fecha de hoy menos unas horas, en ISO-UTC (p. ej. hoy 06:00 UTC):

```bash
mkdir -p scraper_local/logs/resultados
rm -f scraper_local/logs/resultados/.ultimo-visto
cat > scraper_local/logs/resultados/00000000-prueba.json <<'JSON'
{ "runid": "00000000-prueba", "fin_utc": "<HOY>T06:00:00+00:00",
  "redes": ["twitter","tiktok","instagram"], "exit_code": 0,
  "posts_por_red": {"twitter":79,"tiktok":0,"instagram":38}, "warnings_total": 19,
  "cuentas_quemadas": [], "posts_electorales": 78, "nivel": "alerta",
  "titulo": "⚠️ Boca de Urna: corrida con problemas",
  "mensaje": "Sin posts en: tiktok (captcha / cuenta quemada). 19 avisos en la corrida." }
JSON
```

Run: `powershell -NoProfile -File scraper_local/aviso-matutino.ps1`
Expected: aparece el cartel con la línea del resultado. Cerrarlo con "Entendido".

Run de nuevo: `powershell -NoProfile -File scraper_local/aviso-matutino.ps1`
Expected: **no aparece nada** — el `fin_utc` del resultado quedó por detrás del nuevo `.ultimo-visto`, así que ya no está pendiente.

Limpiar el archivo de prueba al terminar: `rm scraper_local/logs/resultados/00000000-prueba.json`

- [ ] **Step 4: Commit**

```bash
git add scraper_local/aviso-matutino.ps1
git commit -m "feat(urna): aviso-matutino.ps1 muestra cartel full-screen de corridas fallidas"
```

---

### Task 4: Config + documentación (Task Scheduler)

**Files:**
- Modify: `scraper_local/config.json`
- Modify: `scraper_local/README.md` (tabla de Task Scheduler, ~línea 150)

**Interfaces:**
- Consumes: `config.json` → `alertas.umbral_warnings` lo lee `correr()` (Task 2, Step 6).
- Produces: nada de código; deja documentado cómo registrar la tarea de las 9h.

- [ ] **Step 1: Agregar el bloque `alertas` a `config.json`**

En `scraper_local/config.json`, agregar la clave `"alertas"` al nivel raíz (junto a `"candidatos": null`):

```json
  "candidatos": null,
  "alertas": { "umbral_warnings": 8 },
```

- [ ] **Step 2: Verificar que el config sigue cargando**

Run: `cd scraper_local && .venv/Scripts/python -c "import json; print(json.load(open('config.json', encoding='utf-8'))['alertas'])"`
Expected: `{'umbral_warnings': 8}`.

- [ ] **Step 3: Documentar la tarea de las 9h en el README**

En `scraper_local/README.md`, agregar una fila a la tabla de **Task Scheduler** (la que hoy tiene las tareas de 10:00 y 16:00):

```markdown
| 09:00 | SMATA Boca de Urna - Aviso matutino (9h) | `powershell -NoProfile -File "<ruta>\scraper_local\aviso-matutino.ps1"` |
```

Y debajo de la tabla, agregar la nota:

```markdown
> [!note] Aviso matutino
> La tarea de las **09:00** muestra un cartel a pantalla completa SOLO si alguna
> corrida salió degradada/fallida desde la última vez que lo cerraste (red en 0,
> cuenta quemada, subida fallida, ≥8 avisos, o si no hubo corrida en ~20 h). Si
> estuvo todo bien, no aparece nada. Debe ser **LogonType Interactive** (usuario
> logueado): la ventana necesita escritorio real. El umbral de avisos se ajusta en
> `config.json` → `alertas.umbral_warnings`. Probarlo sin esperar una falla:
> `aviso-matutino.ps1 -Simular`.
```

- [ ] **Step 4: Commit**

```bash
git add scraper_local/config.json scraper_local/README.md
git commit -m "docs(urna): registrar la tarea de aviso matutino (9h) + umbral configurable"
```

---

## Self-Review

**1. Spec coverage:**
- Registro del resultado por corrida (`run.py` + `alertas.py`) → Tasks 1, 2. ✓
- Regla de 4 condiciones + `exit_code 2` no alerta + umbral configurable → Task 1 (lógica) + Task 2 (lee umbral de cfg) + Task 4 (config). ✓
- Cartel full-screen a las 9h, marca "visto", chequeo "no corrió", `-Simular` → Task 3. ✓
- Tarea programada 09:00 Interactive + README → Task 4. ✓
- `test_alertas.py` con casos reales → Task 1. ✓
- Sin dependencias pip, todo UTF-8/UTC → respetado en Tasks 2 y 3. ✓
- Poda de resultados viejos → Task 2 (`_podar_resultados`). ✓

**2. Placeholder scan:** `<ruta>` en el README es intencional (sigue la convención de las filas existentes de la tabla). Sin TBD/TODO ni pasos sin código.

**3. Type consistency:** `alertas.evaluar(resultado, umbral_warnings=8)` y `alertas.TITULO` se usan igual en `run._armar_resultado`. Las claves del dict `resultado` (`exit_code`, `posts_por_red`, `cuentas_quemadas`, `warnings_total`, `fin_utc`, `nivel`, `mensaje`) coinciden entre `run._armar_resultado` (productor) y `aviso-matutino.ps1` (consumidor). `DIR_RESULTADOS` se define en run.py y se monkeypatchea igual en los tests.
