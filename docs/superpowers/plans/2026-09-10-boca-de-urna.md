# Boca de Urna — Termómetro de Redes · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar una página `/boca-de-urna` que estima el apoyo por candidato desde publicaciones públicas de redes (sentimiento neto vía Gemini), muestra la evidencia, y compara la brecha contra consultoras cargadas por CSV.

**Architecture:** Backend en 3 capas (`main.py` → `electoral.py` → reuso de `normalizer.fetch_posts` → `fetcher.py`). Se extrae el transporte de Gemini a un `gemini_client.py` compartido por `normalizer` y `electoral`. Frontend Next.js: página nueva aislada del monitor actual, gráficos con CSS puro (sin librerías). Todo stateless.

**Tech Stack:** FastAPI + Pydantic (Python), Google Gemini (REST), SerpAPI (vía fetcher existente), `csv` stdlib; Next.js 14 App Router + TypeScript. Tests: pytest (backend), `next build` como gate de tipo/compilación (frontend).

## Global Constraints

- **No agregar dependencias nuevas.** Backend: solo stdlib + lo ya presente (`requests`). Frontend: sin librerías de charts (barras con CSS puro).
- **No tocar `fetcher.py`** (Capa 2 ciega).
- **No cambiar la lógica de `normalizer.py`**: solo extraer el transporte de Gemini e importarlo. Los tests existentes deben quedar verdes.
- **Stateless**: nada se persiste (sin DB, sin histórico).
- **Rótulo institucional**: la sección se llama "Boca de Urna — Termómetro de Redes". Nunca "encuesta" ni "proyección de resultado".
- **Disclaimer verbatim** (obligatorio, siempre visible): *"Este indicador refleja el clima de conversación en redes sociales sobre publicaciones públicas indexadas. No es una muestra representativa del electorado ni una proyección de resultado electoral. Sirve como termómetro direccional, complementario a las encuestas de consultoras."*
- **Umbral de confianza**: `CONF_MIN = 0.5`. Clasificaciones por debajo no cuentan (se reportan aparte).
- **Posturas válidas**: `a_favor` / `en_contra` / `neutro`.
- **Branding**: variables CSS `--smata-*` existentes, tema claro/oscuro, `ThemeToggle`.
- **Comandos**: backend desde `backend/` con el venv activo; frontend desde `frontend/`.

---

### Task 1: Extraer `gemini_client.py` (refactor del transporte de Gemini)

Mueve el transporte de Gemini (HTTP + parseo JSON + cascada de modelos + rotación de API key) de `normalizer.py` a un módulo compartido, sin cambiar comportamiento. `normalizer` pasa a importarlo; `electoral` (Task 3+) lo reusará.

**Files:**
- Create: `backend/gemini_client.py`
- Modify: `backend/normalizer.py` (quitar defs movidas; importar; reemplazar bloque cascada+rotación en `_process_with_gemini`; alias `_gemini_generate`)
- Modify: `backend/test_normalizer.py` (repuntar 9 patches de scoring/rotación al nuevo módulo)
- Create: `backend/test_gemini_client.py`

**Interfaces:**
- Consumes: nada (primer task).
- Produces (lo que Task 3/6 usan):
  - `GEMINI_MODELS: tuple[str, ...]`, `GEMINI_RETRY_STATUSES: tuple[int, ...]`
  - `generate_raw(model: str, api_key: str, prompt: str, timeout: int = 45) -> tuple[str | None, int | None]`
  - `call_gemini_json(model: str, api_key: str, prompt: str) -> tuple[list | None, int | None]`
  - `run_cascade(prompt: str, api_key: str) -> tuple[list | None, int | None]`
  - `run_with_rotation(prompt: str) -> tuple[list | None, int | None]`

- [ ] **Step 1: Crear `backend/gemini_client.py`** con las funciones movidas (cuerpos idénticos a los actuales de `normalizer`, renombradas a públicas) más `run_with_rotation` (extraído del bloque inline de `_process_with_gemini`).

```python
"""
Transporte compartido de Gemini: llamada HTTP cruda, parseo JSON, cascada de
modelos con fallback por status y rotación a la API Key secundaria por cuota.

Extraído de normalizer.py para que la Capa 3 de scoring (normalizer) y la Capa 3
electoral (electoral.py) usen el mismo transporte sin duplicarlo. No conoce el
dominio: recibe un prompt, devuelve la lista JSON que Gemini responde.
"""
import os
import json

import requests

# Cascada de modelos: si el primero da 429/503/404 se prueba el siguiente.
GEMINI_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
)
GEMINI_RETRY_STATUSES = (429, 503, 404)


def generate_raw(model: str, api_key: str, prompt: str, timeout: int = 45) -> tuple[str | None, int | None]:
    """Llamada cruda a un modelo Gemini. Devuelve (texto_sin_fences, http_status_si_error).
    - (texto, None): éxito       - (None, status): error HTTP       - (None, None): error de red."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
        res_data = response.json()
        raw = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return raw, None
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        body_msg = ""
        if e.response is not None:
            try:
                body = e.response.json()
                body_msg = body.get("error", {}).get("message", "") or str(body)[:200]
            except Exception:
                body_msg = (e.response.text or "")[:200]
        print(f"ERROR Gemini[{model}]: HTTP {status} — {body_msg}")
        return None, status
    except Exception as e:
        print(f"ERROR Gemini[{model}]: {e}")
        return None, None


def call_gemini_json(model: str, api_key: str, prompt: str) -> tuple[list | None, int | None]:
    """Llama a un modelo y parsea la respuesta como LISTA JSON (mirror por id).
    - (lista, None): éxito     - (None, status): error HTTP     - (None, None): red/parseo."""
    raw, status = generate_raw(model, api_key, prompt, timeout=45)
    if raw is None:
        return None, status
    try:
        parsed = json.loads(raw)
    except Exception as e:
        print(f"ERROR Gemini[{model}]: parseo JSON falló — {e}")
        return None, None
    if not isinstance(parsed, list):
        print(f"ERROR Gemini[{model}]: respuesta no es lista JSON")
        return None, None
    return parsed, None


def run_cascade(prompt: str, api_key: str) -> tuple[list | None, int | None]:
    """Recorre GEMINI_MODELS con UNA api_key; el primero que responde OK gana; el
    siguiente solo se prueba ante 429/503/404. Devuelve (parsed, last_status)."""
    parsed: list | None = None
    status: int | None = None
    for idx, model in enumerate(GEMINI_MODELS):
        parsed, status = call_gemini_json(model, api_key, prompt)
        if parsed is not None:
            if idx > 0:
                print(f"DEBUG Gemini: fallback EXITOSO con {model}")
            return parsed, status
        if status not in GEMINI_RETRY_STATUSES:
            return None, status
        if idx + 1 < len(GEMINI_MODELS):
            print(f"DEBUG Gemini: HTTP {status} en {model}, probando fallback {GEMINI_MODELS[idx + 1]}")
    return None, status


def run_with_rotation(prompt: str) -> tuple[list | None, int | None]:
    """Cascada con la API Key PRINCIPAL; si la cuota se agotó (429/503) y no quedó
    resultado, rota a la SECUNDARIA y reintenta el mismo prompt. Devuelve
    (parsed, last_status). Sin GEMINI_API_KEY devuelve (None, None)."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY no configurada.")
        return None, None
    parsed, status = run_cascade(prompt, api_key)
    if parsed is None and status in (429, 503):
        secondary_key = os.getenv("GEMINI_API_KEY_SECONDARY", "")
        if secondary_key:
            print("Cuota principal agotada. Rotando a la API de SMATA...")
            parsed, status = run_cascade(prompt, secondary_key)
            if parsed is not None:
                print("DEBUG Gemini: rotación a API secundaria EXITOSA.")
        else:
            print("DEBUG Gemini: cuota principal agotada y GEMINI_API_KEY_SECONDARY no configurada — sin rotación.")
    return parsed, status
```

- [ ] **Step 2: Editar `backend/normalizer.py` — importar del nuevo módulo y quitar las defs movidas.**

Reemplazar el bloque de constantes `GEMINI_MODELS`/`GEMINI_RETRY_STATUSES` (líneas ~45-51) por un import, y **borrar** las defs `_gemini_generate`, `_call_gemini_model`, `_run_gemini_cascade` (líneas ~255-334). Debajo del import de `fetcher` (línea 21) agregar:

```python
from gemini_client import (
    GEMINI_MODELS,
    GEMINI_RETRY_STATUSES,
    generate_raw,
    run_cascade,
    run_with_rotation,
)

# Alias de compat: _run_translation_cascade (abajo) llama a _gemini_generate por
# nombre de módulo, y sus tests parchean normalizer._gemini_generate. Mantener el
# alias preserva ese punto de parcheo sin duplicar la función.
_gemini_generate = generate_raw
```

Dejar intacta la constante `GEMINI_MAX_PER_NETWORK` (es de dominio, no de transporte).

- [ ] **Step 3: Editar `_process_with_gemini` en `normalizer.py` — usar `run_with_rotation`.**

Quitar la lectura temprana de la key (líneas ~475-478):

```python
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY no configurada.")
        return None
```

Y reemplazar el bloque cascada+rotación (líneas ~587-605, desde `scored, status = _run_gemini_cascade(...)` hasta `if scored is None: return None`) por:

```python
    # Transporte compartido: cascada de modelos + rotación a la key secundaria.
    scored, status = run_with_rotation(prompt)
    if scored is None:
        return None
```

- [ ] **Step 4: Repuntar los patches de scoring/rotación en `backend/test_normalizer.py`.**

Agregar el import arriba (junto a `import normalizer as nz`):

```python
import gemini_client as gc
```

Cambiar las **9** ocurrencias de `monkeypatch.setattr(nz, "_call_gemini_model", ...)` por `monkeypatch.setattr(gc, "call_gemini_json", ...)` (tests: `test_process_una_sola_llamada_para_todas_las_redes`, `test_process_descarta_bajo_el_piso`, `test_process_tiktok_sin_deeplink_cae_al_fallback`, `test_process_purga_basura_tiktok`, `test_process_mapea_por_id_y_respeta_url_original`, `test_process_rota_a_secundaria_en_429`, `test_process_rota_tambien_en_503`, `test_process_sin_secundaria_no_rota`, `test_process_no_rota_si_error_no_es_cuota`). También actualizar el helper `_fake_scored` si hace `setattr` — cambiar su target a `gc.call_gemini_json`. **No tocar** los patches de traducción (`nz._gemini_generate`) ni `nz._translate_query_for_country`: siguen funcionando por el alias del Step 2.

- [ ] **Step 5: Crear `backend/test_gemini_client.py`** (smoke test del transporte).

```python
"""Smoke test del transporte extraído: cascada y rotación, con Gemini mockeado."""
import gemini_client as gc


def test_run_cascade_primer_modelo_ok(monkeypatch):
    llamadas = []
    def fake(model, api_key, prompt):
        llamadas.append(model)
        return ([{"id": "Post_0"}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, status = gc.run_cascade("p", "k")
    assert parsed == [{"id": "Post_0"}]
    assert len(llamadas) == 1  # no probó fallback


def test_run_cascade_fallback_en_429(monkeypatch):
    def fake(model, api_key, prompt):
        if model == gc.GEMINI_MODELS[0]:
            return (None, 429)
        return ([{"ok": True}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, _ = gc.run_cascade("p", "k")
    assert parsed == [{"ok": True}]


def test_run_cascade_error_no_reintentable_corta(monkeypatch):
    llamadas = []
    def fake(model, api_key, prompt):
        llamadas.append(model)
        return (None, 400)  # no está en GEMINI_RETRY_STATUSES
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, status = gc.run_cascade("p", "k")
    assert parsed is None and status == 400
    assert len(llamadas) == 1


def test_run_with_rotation_sin_key_devuelve_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gc.run_with_rotation("p") == (None, None)


def test_run_with_rotation_rota_a_secundaria_en_429(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "primary")
    monkeypatch.setenv("GEMINI_API_KEY_SECONDARY", "secondary")
    keys = []
    def fake_cascade(prompt, api_key):
        keys.append(api_key)
        return (None, 429) if api_key == "primary" else ([{"ok": True}], None)
    monkeypatch.setattr(gc, "run_cascade", fake_cascade)
    parsed, _ = gc.run_with_rotation("p")
    assert parsed == [{"ok": True}]
    assert keys == ["primary", "secondary"]
```

- [ ] **Step 6: Correr toda la suite y verificar verde.**

Run: `python -m pytest -q`
Expected: PASS — todos los tests de `test_normalizer.py`, `test_fetcher.py` y el nuevo `test_gemini_client.py` en verde (el refactor no cambió comportamiento).

- [ ] **Step 7: Commit.**

```bash
git add backend/gemini_client.py backend/normalizer.py backend/test_normalizer.py backend/test_gemini_client.py
git commit -m "refactor(gemini): extrae transporte compartido a gemini_client.py"
```

---

### Task 2: Parseo del CSV de consultoras (`parse_pollster_csv`)

Función pura que valida y parsea el texto CSV subido. Filas inválidas se saltean con warning; header inválido es error duro.

**Files:**
- Create: `backend/electoral.py`
- Create: `backend/test_electoral.py`

**Interfaces:**
- Consumes: nada.
- Produces: `parse_pollster_csv(text: str) -> tuple[list[dict], list[str]]`. Cada fila válida: `{"consultora": str, "fecha": str, "candidato": str, "porcentaje": float}`. Segundo elemento: lista de warnings. Lanza `ValueError` si faltan columnas requeridas o el CSV está vacío con contenido no-CSV.

- [ ] **Step 1: Escribir el test que falla** en `backend/test_electoral.py`.

```python
import electoral as el
import pytest


CSV_OK = (
    "consultora,fecha,candidato,porcentaje\n"
    "Consultora X,2026-08-01,Javier Milei,42.5\n"
    "Consultora X,2026-08-01,Axel Kicillof,38,0\n"   # coma decimal
    "Consultora Y,2026-08-15,Javier Milei,40.1\n"
)


def test_parse_csv_happy_path():
    rows, warnings = el.parse_pollster_csv(CSV_OK)
    assert len(rows) == 3
    assert warnings == []
    assert rows[0] == {"consultora": "Consultora X", "fecha": "2026-08-01",
                       "candidato": "Javier Milei", "porcentaje": 42.5}
    assert rows[1]["porcentaje"] == 38.0  # coma decimal normalizada


def test_parse_csv_saltea_filas_invalidas_con_warning():
    csv = ("consultora,fecha,candidato,porcentaje\n"
           "Buena,2026-08-01,Milei,40\n"
           "MalPct,2026-08-01,Milei,no-num\n"
           "MalFecha,ayer,Milei,30\n")
    rows, warnings = el.parse_pollster_csv(csv)
    assert len(rows) == 1 and rows[0]["consultora"] == "Buena"
    assert len(warnings) == 2


def test_parse_csv_header_invalido_es_error():
    with pytest.raises(ValueError):
        el.parse_pollster_csv("foo,bar\n1,2\n")


def test_parse_csv_vacio_devuelve_vacio():
    rows, warnings = el.parse_pollster_csv("")
    assert rows == [] and warnings == []
```

- [ ] **Step 2: Correr el test y verificar que falla.**

Run: `python -m pytest backend/test_electoral.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'electoral'` / `AttributeError: parse_pollster_csv`.

- [ ] **Step 3: Implementar `parse_pollster_csv` en `backend/electoral.py`.**

```python
"""
Capa 3 — Boca de Urna (Termómetro de Redes).

Reutiliza normalizer.fetch_posts (que a su vez usa fetcher/SerpAPI) para traer
publicaciones públicas, corre un análisis electoral con Gemini (candidato +
postura + confianza por post vía gemini_client), agrega en sentimiento neto por
candidato, y compara la brecha contra las consultoras cargadas por CSV.

No habla con SerpAPI ni con Gemini directamente: fetch vía normalizer, transporte
vía gemini_client. Stateless.
"""
import csv
import io
import json
import unicodedata

CONF_MIN = 0.5
POSTURAS_VALIDAS = ("a_favor", "en_contra", "neutro")
CSV_COLUMNS = ("consultora", "fecha", "candidato", "porcentaje")


def _parse_pct(raw: str) -> float:
    """Convierte '42,5' o '42.5' a float. Lanza ValueError si no es numérico."""
    return float(str(raw).strip().replace(",", "."))


def _valid_iso_date(raw: str) -> bool:
    import datetime
    try:
        datetime.date.fromisoformat(str(raw).strip())
        return True
    except ValueError:
        return False


def parse_pollster_csv(text: str) -> tuple[list[dict], list[str]]:
    """CSV de consultoras -> (filas_validas, warnings). Header inválido -> ValueError."""
    text = (text or "").strip()
    if not text:
        return [], []
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    faltantes = [c for c in CSV_COLUMNS if c not in header]
    if faltantes:
        raise ValueError(f"CSV inválido: faltan columnas {faltantes}. Se esperan {list(CSV_COLUMNS)}.")

    rows: list[dict] = []
    warnings: list[str] = []
    for i, raw in enumerate(reader, start=2):  # fila 1 = header
        consultora = (raw.get("consultora") or "").strip()
        fecha = (raw.get("fecha") or "").strip()
        candidato = (raw.get("candidato") or "").strip()
        if not consultora or not candidato:
            warnings.append(f"Fila {i}: consultora o candidato vacío — salteada.")
            continue
        if not _valid_iso_date(fecha):
            warnings.append(f"Fila {i}: fecha '{fecha}' no es YYYY-MM-DD — salteada.")
            continue
        try:
            pct = _parse_pct(raw.get("porcentaje", ""))
        except (TypeError, ValueError):
            warnings.append(f"Fila {i}: porcentaje '{raw.get('porcentaje')}' no numérico — salteada.")
            continue
        rows.append({"consultora": consultora, "fecha": fecha,
                     "candidato": candidato, "porcentaje": pct})
    return rows, warnings
```

- [ ] **Step 4: Correr los tests y verificar que pasan.**

Run: `python -m pytest backend/test_electoral.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit.**

```bash
git add backend/electoral.py backend/test_electoral.py
git commit -m "feat(urna): parseo y validación del CSV de consultoras"
```

---

### Task 3: Análisis electoral con Gemini (`analyze_posts_electoral`)

Construye el prompt electoral, llama al transporte compartido y devuelve el mirror por id, saneando posturas.

**Files:**
- Modify: `backend/electoral.py`
- Modify: `backend/test_electoral.py`

**Interfaces:**
- Consumes: `gemini_client.run_with_rotation` (Task 1).
- Produces: `analyze_posts_electoral(posts_by_id: dict[str, dict]) -> list[dict] | None`. Cada item: `{"id": str, "candidatos": [{"nombre": str, "postura": str, "confianza": float}], "cita": str, "es_electoral": bool}`. Devuelve `None` si el transporte falló (upstream). `posts_by_id` mapea `"Post_i" -> post` (post con `text`/`network`).

- [ ] **Step 1: Escribir el test que falla** (append a `backend/test_electoral.py`).

```python
def _analysis_fake(items):
    """Devuelve un run_with_rotation fake que responde un mirror fijo."""
    return lambda prompt: (items, None)


def test_analyze_mapea_por_id_y_sanea_postura(monkeypatch):
    import gemini_client as gc
    posts_by_id = {
        "Post_0": {"text": "Milei la rompe", "network": "twitter"},
        "Post_1": {"text": "Basta de Milei", "network": "instagram"},
    }
    mirror = [
        {"id": "Post_0", "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}],
         "cita": "Milei la rompe", "es_electoral": True},
        {"id": "Post_1", "candidatos": [{"nombre": "Javier Milei", "postura": "INVALIDA", "confianza": 0.8}],
         "cita": "Basta de Milei", "es_electoral": True},
    ]
    monkeypatch.setattr(gc, "run_with_rotation", _analysis_fake(mirror))
    out = el.analyze_posts_electoral(posts_by_id)
    assert out[0]["candidatos"][0]["postura"] == "a_favor"
    # postura inválida se descarta -> candidato sin postura válida queda fuera
    assert out[1]["candidatos"] == []


def test_analyze_upstream_falla_devuelve_none(monkeypatch):
    import gemini_client as gc
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (None, 503))
    assert el.analyze_posts_electoral({"Post_0": {"text": "x", "network": "twitter"}}) is None


def test_analyze_ids_inventados_se_ignoran(monkeypatch):
    import gemini_client as gc
    monkeypatch.setattr(gc, "run_with_rotation",
                        _analysis_fake([{"id": "Post_999", "candidatos": [], "cita": "", "es_electoral": False}]))
    out = el.analyze_posts_electoral({"Post_0": {"text": "x", "network": "twitter"}})
    assert out == []  # el id inventado no matchea ningún post
```

- [ ] **Step 2: Correr y verificar que falla.**

Run: `python -m pytest backend/test_electoral.py -k analyze -q`
Expected: FAIL con `AttributeError: analyze_posts_electoral`.

- [ ] **Step 3: Implementar en `backend/electoral.py`** (agregar import y función).

Al tope del archivo, junto a los imports:

```python
import gemini_client
```

Función:

```python
def _build_electoral_prompt(items_para_prompt: list[dict]) -> str:
    items_text = json.dumps(items_para_prompt, ensure_ascii=False, indent=2)
    return f"""Sos un analista de opinión pública que evalúa publicaciones de redes sociales del ámbito argentino de cara a las próximas elecciones presidenciales.

Vas a recibir una lista de publicaciones en JSON. Cada una tiene un "id" único ("Post_0", "Post_1", ...), su "texto" y la "red".

REGLA DE AISLAMIENTO (OBLIGATORIA): Evaluá cada publicación de forma totalmente AISLADA e INDEPENDIENTE. Está PROHIBIDO que una publicación influya en el análisis de otra. Tratá cada "id" como un caso separado.

Por cada publicación determiná:
1. "es_electoral": true solo si la publicación habla de candidatos, partidos o la contienda electoral presidencial argentina; false si es ruido, spam u otro tema.
2. "candidatos": lista de los candidatos presidenciales mencionados. Por cada uno:
   - "nombre": el nombre COMPLETO y CANÓNICO del candidato (ej. si dice "Milei" o "el León", devolvé "Javier Milei"). Unificá alias y apodos al nombre canónico.
   - "postura": la postura del AUTOR del posteo hacia ese candidato. Exactamente uno de: "a_favor", "en_contra", "neutro".
   - "confianza": número entre 0 y 1 con tu certeza sobre esa postura.
   Si no hay candidatos, devolvé [].
3. "cita": el fragmento textual breve del posteo que justifica la postura (o "" si no aplica).

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código) que sea un ESPEJO EXACTO de los IDs recibidos: un objeto por publicación, con su mismo "id". No agregues ni omitas ninguno. Formato exacto:
[
  {{ "id": "Post_0", "es_electoral": true, "candidatos": [{{ "nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9 }}], "cita": "..." }}
]

Publicaciones a evaluar:
{items_text}"""


def analyze_posts_electoral(posts_by_id: dict[str, dict]) -> list[dict] | None:
    """Clasifica cada post (candidato + postura + confianza) vía Gemini. Devuelve
    el mirror saneado por id, o None si el transporte falló (upstream)."""
    if not posts_by_id:
        return []
    items_para_prompt = [
        {"id": pid, "red": src.get("network", ""), "texto": src.get("text", "") or ""}
        for pid, src in posts_by_id.items()
    ]
    prompt = _build_electoral_prompt(items_para_prompt)
    parsed, _status = gemini_client.run_with_rotation(prompt)
    if parsed is None:
        return None

    out: list[dict] = []
    for item in parsed:
        pid = item.get("id", "") or ""
        if pid not in posts_by_id:
            continue  # id inventado o repetido por la IA
        candidatos_saneados = []
        for c in item.get("candidatos", []) or []:
            nombre = (c.get("nombre") or "").strip()
            postura = (c.get("postura") or "").strip().lower()
            if not nombre or postura not in POSTURAS_VALIDAS:
                continue
            try:
                conf = float(c.get("confianza", 0))
            except (TypeError, ValueError):
                conf = 0.0
            candidatos_saneados.append({"nombre": nombre, "postura": postura, "confianza": conf})
        out.append({
            "id": pid,
            "candidatos": candidatos_saneados,
            "cita": (item.get("cita") or "").strip(),
            "es_electoral": bool(item.get("es_electoral", False)),
        })
    return out
```

- [ ] **Step 4: Correr y verificar que pasan.**

Run: `python -m pytest backend/test_electoral.py -k analyze -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit.**

```bash
git add backend/electoral.py backend/test_electoral.py
git commit -m "feat(urna): análisis electoral con Gemini (candidato+postura+confianza)"
```

---

### Task 4: Canonicalización + agregación de sentimiento neto

Unifica nombres por clave normalizada y calcula el % por candidato (neto, con fallback a volumen). Aparte, arma la evidencia.

**Files:**
- Modify: `backend/electoral.py`
- Modify: `backend/test_electoral.py`

**Interfaces:**
- Consumes: la salida de `analyze_posts_electoral` (Task 3).
- Produces:
  - `canonical_key(nombre: str) -> str` (sin acentos, minúsculas, espacios colapsados).
  - `aggregate_net_sentiment(analysis: list[dict]) -> tuple[list[dict], int, bool]` → `(candidatos, baja_confianza, fallback_volumen)`. Cada candidato: `{"nombre": str, "pct": float, "pos": int, "neg": int, "neu": int, "menciones": int}`, ordenado por `pct` desc.
  - `build_evidence(analysis: list[dict], posts_by_id: dict[str, dict], por_candidato: int = 5) -> list[dict]`. Cada item: `{"candidato": str, "postura": str, "cita": str, "post": dict}`.

- [ ] **Step 1: Escribir el test que falla** (append a `backend/test_electoral.py`).

```python
def test_canonical_key_unifica_acentos_y_may():
    assert el.canonical_key("Sergio Massa") == el.canonical_key("  sergio  massa ")
    assert el.canonical_key("Patricia Bullrich") == el.canonical_key("PATRICIA BULLRICH")


def test_aggregate_neto_y_confianza():
    analysis = [
        {"id": "Post_0", "es_electoral": True, "cita": "a",
         "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}]},
        {"id": "Post_1", "es_electoral": True, "cita": "b",
         "candidatos": [{"nombre": "javier  milei", "postura": "a_favor", "confianza": 0.8}]},
        {"id": "Post_2", "es_electoral": True, "cita": "c",
         "candidatos": [{"nombre": "Milei", "postura": "en_contra", "confianza": 0.7}]},
        {"id": "Post_3", "es_electoral": True, "cita": "d",
         "candidatos": [{"nombre": "Axel Kicillof", "postura": "a_favor", "confianza": 0.9}]},
        {"id": "Post_4", "es_electoral": True, "cita": "e",
         "candidatos": [{"nombre": "Milei", "postura": "a_favor", "confianza": 0.2}]},  # baja conf: descartada
    ]
    candidatos, baja_conf, fallback = el.aggregate_net_sentiment(analysis)
    assert baja_conf == 1 and fallback is False
    milei = next(c for c in candidatos if c["nombre"] == "Javier Milei")
    assert (milei["pos"], milei["neg"], milei["menciones"]) == (2, 1, 3)  # net = 1
    kici = next(c for c in candidatos if el.canonical_key(c["nombre"]) == el.canonical_key("Axel Kicillof"))
    assert kici["pos"] == 1  # net = 1
    # net Milei = 1, net Kicillof = 1 -> 50/50
    assert milei["pct"] == 50.0 and kici["pct"] == 50.0
    assert candidatos == sorted(candidatos, key=lambda c: c["pct"], reverse=True)


def test_aggregate_fallback_a_volumen_si_todos_negativos():
    analysis = [
        {"id": "Post_0", "candidatos": [{"nombre": "A", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
        {"id": "Post_1", "candidatos": [{"nombre": "A", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
        {"id": "Post_2", "candidatos": [{"nombre": "B", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
    ]
    candidatos, _bc, fallback = el.aggregate_net_sentiment(analysis)
    assert fallback is True
    a = next(c for c in candidatos if c["nombre"] == "A")
    assert a["pct"] == 66.7  # 2 de 3 menciones


def test_build_evidence_ordena_por_confianza_y_adjunta_post():
    analysis = [
        {"id": "Post_0", "cita": "cita floja",
         "candidatos": [{"nombre": "Milei", "postura": "a_favor", "confianza": 0.6}]},
        {"id": "Post_1", "cita": "cita fuerte",
         "candidatos": [{"nombre": "Milei", "postura": "a_favor", "confianza": 0.95}]},
    ]
    posts_by_id = {
        "Post_0": {"text": "t0", "network": "twitter", "post_url": "u0", "author": "a0", "author_url": "", "date": ""},
        "Post_1": {"text": "t1", "network": "tiktok", "post_url": "u1", "author": "a1", "author_url": "", "date": ""},
    }
    ev = el.build_evidence(analysis, posts_by_id)
    assert ev[0]["cita"] == "cita fuerte"  # mayor confianza primero
    assert ev[0]["post"]["post_url"] == "u1"
    assert ev[0]["candidato"] == "Milei"
```

- [ ] **Step 2: Correr y verificar que falla.**

Run: `python -m pytest backend/test_electoral.py -k "canonical or aggregate or evidence" -q`
Expected: FAIL con `AttributeError`.

- [ ] **Step 3: Implementar en `backend/electoral.py`.**

```python
def canonical_key(nombre: str) -> str:
    """Clave de merge: sin acentos, minúsculas, espacios colapsados."""
    s = unicodedata.normalize("NFKD", (nombre or "").strip().lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.split())


def aggregate_net_sentiment(analysis: list[dict]) -> tuple[list[dict], int, bool]:
    """Agrega por candidato en sentimiento neto. Devuelve (candidatos, baja_confianza, fallback_volumen)."""
    acc: dict[str, dict] = {}
    baja_confianza = 0
    for item in analysis:
        for c in item.get("candidatos", []) or []:
            if c.get("confianza", 0) < CONF_MIN:
                baja_confianza += 1
                continue
            key = canonical_key(c["nombre"])
            entry = acc.setdefault(key, {"nombre": c["nombre"], "pos": 0, "neg": 0, "neu": 0, "menciones": 0})
            entry["menciones"] += 1
            if c["postura"] == "a_favor":
                entry["pos"] += 1
            elif c["postura"] == "en_contra":
                entry["neg"] += 1
            else:
                entry["neu"] += 1

    entries = list(acc.values())
    for e in entries:
        e["net"] = e["pos"] - e["neg"]
    suma_neto = sum(max(e["net"], 0) for e in entries)
    fallback_volumen = suma_neto <= 0
    suma_menciones = sum(e["menciones"] for e in entries)

    candidatos: list[dict] = []
    for e in entries:
        if fallback_volumen:
            pct = (e["menciones"] / suma_menciones * 100) if suma_menciones else 0.0
        else:
            pct = (max(e["net"], 0) / suma_neto * 100)
        candidatos.append({
            "nombre": e["nombre"], "pct": round(pct, 1),
            "pos": e["pos"], "neg": e["neg"], "neu": e["neu"], "menciones": e["menciones"],
        })
    candidatos.sort(key=lambda c: c["pct"], reverse=True)
    return candidatos, baja_confianza, fallback_volumen


def build_evidence(analysis: list[dict], posts_by_id: dict[str, dict], por_candidato: int = 5) -> list[dict]:
    """Empareja cada mención (conf >= CONF_MIN) con su post + cita, ordenada por
    confianza desc, capando a `por_candidato` citas por candidato."""
    _POST_FIELDS = ("network", "author", "author_url", "text", "post_url", "date")
    filas: list[tuple[float, dict]] = []
    for item in analysis:
        src = posts_by_id.get(item.get("id", ""))
        if src is None:
            continue
        post_subset = {k: src.get(k, "") for k in _POST_FIELDS}
        for c in item.get("candidatos", []) or []:
            if c.get("confianza", 0) < CONF_MIN:
                continue
            filas.append((c["confianza"], {
                "candidato": c["nombre"], "postura": c["postura"],
                "cita": item.get("cita", "") or (src.get("text", "") or ""),
                "post": post_subset,
            }))
    filas.sort(key=lambda t: t[0], reverse=True)
    vistos: dict[str, int] = {}
    evidencia: list[dict] = []
    for _conf, fila in filas:
        key = canonical_key(fila["candidato"])
        if vistos.get(key, 0) >= por_candidato:
            continue
        vistos[key] = vistos.get(key, 0) + 1
        evidencia.append(fila)
    return evidencia
```

- [ ] **Step 4: Correr y verificar que pasan.**

Run: `python -m pytest backend/test_electoral.py -k "canonical or aggregate or evidence" -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit.**

```bash
git add backend/electoral.py backend/test_electoral.py
git commit -m "feat(urna): canonicalización, sentimiento neto y evidencia"
```

---

### Task 5: Comparación redes vs consultoras (`compare_vs_pollsters`)

Cruza los % de redes con los del CSV por candidato (última fecha por consultora), calcula brechas y avisa los no reconciliados.

**Files:**
- Modify: `backend/electoral.py`
- Modify: `backend/test_electoral.py`

**Interfaces:**
- Consumes: `candidatos` (Task 4) y `pollster_rows` (Task 2).
- Produces: `compare_vs_pollsters(candidatos: list[dict], pollster_rows: list[dict]) -> tuple[list[dict], list[str]]`. Cada comparación: `{"candidato": str, "redes_pct": float, "consultoras": [{"consultora": str, "pct": float, "gap": float}], "promedio_consultoras": float | None, "gap_promedio": float | None}`. Segundo elemento: warnings de nombres no reconciliados.

- [ ] **Step 1: Escribir el test que falla** (append a `backend/test_electoral.py`).

```python
def test_compare_calcula_gap_y_promedio():
    candidatos = [{"nombre": "Javier Milei", "pct": 44.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    rows = [
        {"consultora": "X", "fecha": "2026-08-01", "candidato": "milei", "porcentaje": 42.0},
        {"consultora": "X", "fecha": "2026-08-20", "candidato": "Milei", "porcentaje": 40.0},  # más reciente gana
        {"consultora": "Y", "fecha": "2026-08-10", "candidato": "Javier Milei", "porcentaje": 46.0},
    ]
    comp, warnings = el.compare_vs_pollsters(candidatos, rows)
    milei = comp[0]
    consultora_x = next(c for c in milei["consultoras"] if c["consultora"] == "X")
    assert consultora_x["pct"] == 40.0 and consultora_x["gap"] == 4.0  # 44 - 40
    assert milei["promedio_consultoras"] == 43.0  # (40 + 46)/2
    assert milei["gap_promedio"] == 1.0           # 44 - 43


def test_compare_avisa_no_reconciliados():
    candidatos = [{"nombre": "Milei", "pct": 44.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    rows = [{"consultora": "X", "fecha": "2026-08-01", "candidato": "Kicillof", "porcentaje": 30.0}]
    comp, warnings = el.compare_vs_pollsters(candidatos, rows)
    assert comp[0]["consultoras"] == [] and comp[0]["promedio_consultoras"] is None
    assert any("Kicillof" in w for w in warnings)   # candidato del CSV sin par en redes


def test_compare_sin_csv_devuelve_solo_redes():
    candidatos = [{"nombre": "Milei", "pct": 100.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    comp, warnings = el.compare_vs_pollsters(candidatos, [])
    assert comp[0]["consultoras"] == [] and warnings == []
```

- [ ] **Step 2: Correr y verificar que falla.**

Run: `python -m pytest backend/test_electoral.py -k compare -q`
Expected: FAIL con `AttributeError: compare_vs_pollsters`.

- [ ] **Step 3: Implementar en `backend/electoral.py`.**

```python
def compare_vs_pollsters(candidatos: list[dict], pollster_rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Cruza los % de redes con los del CSV por candidato (última fecha por
    consultora). Devuelve (comparacion, warnings)."""
    # Última fecha por (candidato_key, consultora).
    latest: dict[tuple[str, str], dict] = {}
    for r in pollster_rows:
        k = (canonical_key(r["candidato"]), r["consultora"])
        if k not in latest or r["fecha"] > latest[k]["fecha"]:
            latest[k] = r
    # Agrupar por candidato_key -> {consultora: pct}.
    por_candidato: dict[str, dict[str, float]] = {}
    for (cand_key, consultora), r in latest.items():
        por_candidato.setdefault(cand_key, {})[consultora] = r["porcentaje"]

    comparacion: list[dict] = []
    keys_redes: set[str] = set()
    for c in candidatos:
        key = canonical_key(c["nombre"])
        keys_redes.add(key)
        consultoras_pct = por_candidato.get(key, {})
        consultoras = [
            {"consultora": nombre, "pct": pct, "gap": round(c["pct"] - pct, 1)}
            for nombre, pct in sorted(consultoras_pct.items())
        ]
        if consultoras_pct:
            promedio = round(sum(consultoras_pct.values()) / len(consultoras_pct), 1)
            gap_promedio = round(c["pct"] - promedio, 1)
        else:
            promedio, gap_promedio = None, None
        comparacion.append({
            "candidato": c["nombre"], "redes_pct": c["pct"], "consultoras": consultoras,
            "promedio_consultoras": promedio, "gap_promedio": gap_promedio,
        })

    warnings: list[str] = []
    for cand_key, consultoras_pct in por_candidato.items():
        if cand_key not in keys_redes:
            nombre = next(r["candidato"] for r in pollster_rows if canonical_key(r["candidato"]) == cand_key)
            warnings.append(f"'{nombre}' aparece en el CSV de consultoras pero no se detectó en redes.")
    return comparacion, warnings
```

- [ ] **Step 4: Correr y verificar que pasan.**

Run: `python -m pytest backend/test_electoral.py -k compare -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit.**

```bash
git add backend/electoral.py backend/test_electoral.py
git commit -m "feat(urna): comparación de brecha redes vs consultoras"
```

---

### Task 6: Orquestación (`run_boca_de_urna`)

Ensambla todo: fetch (reuso de `normalizer.fetch_posts`) → análisis → agregación → evidencia → comparación → `meta` con disclaimer y warnings.

**Files:**
- Modify: `backend/electoral.py`
- Modify: `backend/test_electoral.py`

**Interfaces:**
- Consumes: `normalizer.fetch_posts` (existente) y todas las funciones de Tasks 2-5.
- Produces: `run_boca_de_urna(keywords, networks, date, country, pollster_csv) -> dict` con `{candidatos, evidencia, comparacion, meta}`. Reexporta `UpstreamUnavailableError`. Lanza `ValueError` si el CSV tiene header inválido.

- [ ] **Step 1: Escribir el test que falla** (append a `backend/test_electoral.py`).

```python
def test_run_boca_de_urna_flujo_completo(monkeypatch):
    import normalizer, gemini_client as gc
    posts = [
        {"id": "1", "network": "twitter", "author": "a", "author_url": "", "text": "Milei la rompe",
         "date": "", "post_url": "u1", "relevance_score": 80, "relevance_level": "alta",
         "matched_terms": [], "video_url": None},
        {"id": "2", "network": "instagram", "author": "b", "author_url": "", "text": "Kicillof presidente",
         "date": "", "post_url": "u2", "relevance_score": 70, "relevance_level": "alta",
         "matched_terms": [], "video_url": None},
    ]
    monkeypatch.setattr(normalizer, "fetch_posts", lambda **kw: posts)
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: ([
        {"id": "Post_0", "es_electoral": True, "cita": "Milei la rompe",
         "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}]},
        {"id": "Post_1", "es_electoral": True, "cita": "Kicillof presidente",
         "candidatos": [{"nombre": "Axel Kicillof", "postura": "a_favor", "confianza": 0.9}]},
    ], None))

    csv = ("consultora,fecha,candidato,porcentaje\n"
           "X,2026-08-01,Javier Milei,48\n")
    out = el.run_boca_de_urna(keywords=["elecciones"], networks=["twitter", "instagram"],
                              date=None, country="ar", pollster_csv=csv)
    assert out["meta"]["total_posts"] == 2 and out["meta"]["posts_electorales"] == 2
    assert el.DISCLAIMER in out["meta"]["disclaimer"]
    assert {c["nombre"] for c in out["candidatos"]} == {"Javier Milei", "Axel Kicillof"}
    assert len(out["evidencia"]) == 2
    milei_comp = next(c for c in out["comparacion"] if el.canonical_key(c["candidato"]) == el.canonical_key("Javier Milei"))
    assert milei_comp["consultoras"][0]["consultora"] == "X"


def test_run_boca_de_urna_cero_posts(monkeypatch):
    import normalizer
    monkeypatch.setattr(normalizer, "fetch_posts", lambda **kw: [])
    out = el.run_boca_de_urna(keywords=["x"], networks=["twitter"], date=None, country="ar", pollster_csv="")
    assert out["candidatos"] == [] and out["comparacion"] == []
    assert any("publicaciones" in w.lower() for w in out["meta"]["warnings"])


def test_run_boca_de_urna_upstream_falla_propaga(monkeypatch):
    import normalizer, gemini_client as gc
    posts = [{"id": "1", "network": "twitter", "author": "", "author_url": "", "text": "x", "date": "",
              "post_url": "u", "relevance_score": 50, "relevance_level": "media", "matched_terms": [], "video_url": None}]
    monkeypatch.setattr(normalizer, "fetch_posts", lambda **kw: posts)
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (None, 503))
    with pytest.raises(el.UpstreamUnavailableError):
        el.run_boca_de_urna(keywords=["x"], networks=["twitter"], date=None, country="ar", pollster_csv="")
```

- [ ] **Step 2: Correr y verificar que falla.**

Run: `python -m pytest backend/test_electoral.py -k run_boca -q`
Expected: FAIL con `AttributeError: run_boca_de_urna` / `DISCLAIMER`.

- [ ] **Step 3: Implementar en `backend/electoral.py`** (import + constantes + función).

Agregar el import y reexportar la excepción al tope:

```python
import normalizer
from normalizer import UpstreamUnavailableError  # reexport para el endpoint

DISCLAIMER = (
    "Este indicador refleja el clima de conversación en redes sociales sobre "
    "publicaciones públicas indexadas. No es una muestra representativa del "
    "electorado ni una proyección de resultado electoral. Sirve como termómetro "
    "direccional, complementario a las encuestas de consultoras."
)
```

Función de orquestación:

```python
def run_boca_de_urna(keywords: list[str], networks: list[str], date: str | None,
                     country: str, pollster_csv: str) -> dict:
    """Orquesta la boca de urna. Reusa normalizer.fetch_posts para el corpus,
    corre el análisis electoral y arma el payload. Lanza UpstreamUnavailableError
    (Gemini/SerpAPI caído) o ValueError (CSV con header inválido)."""
    # 1) CSV primero: si el header es inválido, cortamos con ValueError (-> 400).
    pollster_rows, csv_warnings = parse_pollster_csv(pollster_csv)

    # 2) Corpus: reuso del fetch del monitor en modo amplio (no SMATA).
    termino = " ".join([k for k in keywords if k and k.strip()]).strip() or "elecciones presidenciales"
    posts = normalizer.fetch_posts(
        termino=termino, fecha_desde=date, smata_mode=False,
        keywords=keywords, accounts=[], networks=networks, country=country,
    )

    warnings = list(csv_warnings)
    if not posts:
        warnings.append("No se encontraron publicaciones para el término buscado.")
        return {"candidatos": [], "evidencia": [], "comparacion": [],
                "meta": {"total_posts": 0, "posts_electorales": 0,
                         "disclaimer": DISCLAIMER, "warnings": warnings}}

    # 3) Análisis electoral (ids estables Post_i).
    posts_by_id = {f"Post_{i}": p for i, p in enumerate(posts)}
    analysis = analyze_posts_electoral(posts_by_id)
    if analysis is None:
        raise UpstreamUnavailableError(
            "Gemini no está disponible (cuota agotada o servicio caído). Reintentá en unos minutos.")

    posts_electorales = sum(1 for a in analysis if a.get("es_electoral") and a.get("candidatos"))

    # 4) Agregación + evidencia + comparación.
    candidatos, baja_conf, fallback_vol = aggregate_net_sentiment(analysis)
    evidencia = build_evidence(analysis, posts_by_id)
    comparacion, comp_warnings = compare_vs_pollsters(candidatos, pollster_rows)
    warnings.extend(comp_warnings)

    if not candidatos:
        warnings.append("No se detectaron candidatos en las publicaciones analizadas.")
    if fallback_vol and candidatos:
        warnings.append("Sentimiento neto no discriminó (todos ≤ 0): el % se calculó por volumen de menciones.")
    if baja_conf:
        warnings.append(f"{baja_conf} mención(es) descartada(s) por baja confianza (< {CONF_MIN}).")

    return {
        "candidatos": candidatos, "evidencia": evidencia, "comparacion": comparacion,
        "meta": {"total_posts": len(posts), "posts_electorales": posts_electorales,
                 "disclaimer": DISCLAIMER, "warnings": warnings},
    }
```

> **Nota de import**: `electoral` importa `normalizer` (que ya importa `gemini_client`). No hay ciclo: `gemini_client` no importa a ninguno de los dos. Los tests parchean `normalizer.fetch_posts` y `gemini_client.run_with_rotation`.

- [ ] **Step 4: Correr toda la suite de electoral y verificar que pasa.**

Run: `python -m pytest backend/test_electoral.py -q`
Expected: PASS (todos los tests de Tasks 2-6).

- [ ] **Step 5: Commit.**

```bash
git add backend/electoral.py backend/test_electoral.py
git commit -m "feat(urna): orquestación run_boca_de_urna (fetch+análisis+comparación)"
```

---

### Task 7: Endpoint `POST /api/boca-de-urna`

Expone la orquestación en la Capa 1 con el mismo estilo de manejo de errores que `/api/search`.

**Files:**
- Modify: `backend/main.py`
- Create: `backend/test_main_urna.py`

**Interfaces:**
- Consumes: `electoral.run_boca_de_urna`, `electoral.UpstreamUnavailableError`.
- Produces: endpoint HTTP `POST /api/boca-de-urna`.

- [ ] **Step 1: Escribir el test que falla** en `backend/test_main_urna.py`.

```python
"""Tests del endpoint /api/boca-de-urna con la orquestación mockeada."""
from fastapi.testclient import TestClient
import main
import electoral


def _client():
    return TestClient(main.app)


def test_endpoint_ok(monkeypatch):
    payload_out = {"candidatos": [{"nombre": "Milei", "pct": 55.0, "pos": 5, "neg": 1, "neu": 0, "menciones": 6}],
                   "evidencia": [], "comparacion": [],
                   "meta": {"total_posts": 6, "posts_electorales": 6, "disclaimer": "x", "warnings": []}}
    monkeypatch.setattr(electoral, "run_boca_de_urna", lambda **kw: payload_out)
    res = _client().post("/api/boca-de-urna", json={"keywords": ["elecciones"], "networks": ["twitter"],
                                                    "date": None, "country": "ar", "pollster_csv": ""})
    assert res.status_code == 200
    assert res.json()["candidatos"][0]["nombre"] == "Milei"


def test_endpoint_csv_invalido_es_400(monkeypatch):
    def boom(**kw):
        raise ValueError("CSV inválido: faltan columnas ['porcentaje'].")
    monkeypatch.setattr(electoral, "run_boca_de_urna", boom)
    res = _client().post("/api/boca-de-urna", json={"keywords": ["x"], "networks": ["twitter"],
                                                    "date": None, "country": "ar", "pollster_csv": "malo"})
    assert res.status_code == 400
    assert "CSV" in res.json()["detail"]


def test_endpoint_upstream_es_503(monkeypatch):
    def boom(**kw):
        raise electoral.UpstreamUnavailableError("Gemini caído")
    monkeypatch.setattr(electoral, "run_boca_de_urna", boom)
    res = _client().post("/api/boca-de-urna", json={"keywords": ["x"], "networks": ["twitter"],
                                                    "date": None, "country": "ar", "pollster_csv": ""})
    assert res.status_code == 503
```

- [ ] **Step 2: Correr y verificar que falla.**

Run: `python -m pytest backend/test_main_urna.py -q`
Expected: FAIL con 404 (endpoint inexistente) en `test_endpoint_ok`.

> Si falta `TestClient`: `pip install httpx` (ya viene con FastAPI/starlette en dev). Está en `requirements-dev.txt` o instalable sin sumar deps de runtime.

- [ ] **Step 3: Implementar en `backend/main.py`.**

Agregar el import arriba (junto a los otros):

```python
import electoral
```

Y agregar el endpoint (después de `/api/search`, antes de `/api/generate-docx`):

```python
class BocaDeUrnaRequest(BaseModel):
    keywords: List[str] = []
    networks: List[str] = []
    date: Optional[str] = None
    country: str = "ar"
    pollster_csv: str = ""


@app.post("/api/boca-de-urna")
async def boca_de_urna_endpoint(request: BocaDeUrnaRequest):
    try:
        return electoral.run_boca_de_urna(
            keywords=request.keywords,
            networks=request.networks,
            date=request.date,
            country=(request.country or "ar").strip().lower(),
            pollster_csv=request.pollster_csv,
        )
    except HTTPException:
        raise
    except ValueError as e:
        # CSV con header inválido u otro dato inutilizable del usuario.
        raise HTTPException(status_code=400, detail=str(e))
    except electoral.UpstreamUnavailableError as e:
        print(f"Upstream no disponible (boca de urna): {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        print(f"Error interno (boca de urna): {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 4: Correr y verificar que pasa + suite completa.**

Run: `python -m pytest -q`
Expected: PASS (backend completo, incluidos los nuevos endpoints y el refactor).

- [ ] **Step 5: Commit.**

```bash
git add backend/main.py backend/test_main_urna.py
git commit -m "feat(urna): endpoint POST /api/boca-de-urna"
```

---

### Task 8: Cliente HTTP y tipos del frontend (`lib/urnaApi.ts`)

Cliente del endpoint con los tipos del payload y el mismo manejo de cold-start que `lib/api.ts`.

**Files:**
- Create: `frontend/lib/urnaApi.ts`

**Interfaces:**
- Produces: tipos `UrnaRequest`, `UrnaResponse` (y anidados) + `runBocaDeUrna(req, onStatus?)`.

- [ ] **Step 1: Crear `frontend/lib/urnaApi.ts`.**

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Postura = "a_favor" | "en_contra" | "neutro";

export interface UrnaCandidato {
  nombre: string; pct: number; pos: number; neg: number; neu: number; menciones: number;
}
export interface UrnaPost {
  network: string; author: string; author_url: string; text: string; post_url: string; date: string;
}
export interface UrnaEvidencia {
  candidato: string; postura: Postura; cita: string; post: UrnaPost;
}
export interface UrnaConsultora { consultora: string; pct: number; gap: number; }
export interface UrnaComparacion {
  candidato: string; redes_pct: number; consultoras: UrnaConsultora[];
  promedio_consultoras: number | null; gap_promedio: number | null;
}
export interface UrnaMeta {
  total_posts: number; posts_electorales: number; disclaimer: string; warnings: string[];
}
export interface UrnaResponse {
  candidatos: UrnaCandidato[]; evidencia: UrnaEvidencia[];
  comparacion: UrnaComparacion[]; meta: UrnaMeta;
}
export interface UrnaRequest {
  keywords: string[]; networks: string[]; date: string | null; country: string; pollster_csv: string;
}

export type UrnaStatus = "connecting" | "waking";
const TIMEOUT_MS = 120_000;

async function post(path: string, body: unknown, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

export async function runBocaDeUrna(
  req: UrnaRequest,
  onStatus?: (s: UrnaStatus) => void,
): Promise<UrnaResponse> {
  const attempt = async (): Promise<UrnaResponse> => {
    const res = await post("/api/boca-de-urna", req, TIMEOUT_MS);
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json())?.detail || ""; } catch { /* sin body */ }
      const err = new Error(detail || `Falló: ${res.status} ${res.statusText}`) as Error & { fromResponse?: boolean };
      err.fromResponse = true;
      throw err;
    }
    return res.json();
  };

  onStatus?.("connecting");
  try {
    return await attempt();
  } catch {
    onStatus?.("waking");
    try {
      return await attempt();
    } catch (e) {
      const err = e as Error & { fromResponse?: boolean };
      if (err?.fromResponse && err.message && !err.message.startsWith("Falló:")) throw new Error(err.message);
      throw new Error("No se pudo conectar con el servidor. Puede estar despertando del modo reposo; esperá unos segundos y reintentá.");
    }
  }
}
```

- [ ] **Step 2: Verificar que compila (typecheck).**

Run (desde `frontend/`): `npx tsc --noEmit`
Expected: sin errores en `lib/urnaApi.ts`.

- [ ] **Step 3: Commit.**

```bash
git add frontend/lib/urnaApi.ts
git commit -m "feat(urna): cliente HTTP y tipos del frontend"
```

---

### Task 9: Página, navegación, panel de parámetros y disclaimer

Ruta nueva con la barra de parámetros (keywords, país, fecha, upload CSV), el disclaimer, y el fetch cableado (todavía volcando JSON crudo para verificar el flujo).

**Files:**
- Create: `frontend/components/urna/DisclaimerBanner.tsx`
- Create: `frontend/components/urna/UrnaParamsBar.tsx`
- Create: `frontend/app/boca-de-urna/page.tsx`
- Modify: `frontend/app/layout.tsx` (links de navegación)

**Interfaces:**
- Consumes: `runBocaDeUrna`, tipos de `lib/urnaApi.ts`.
- Produces: `DisclaimerBanner`, `UrnaParamsBar` (props abajo), la página, y los componentes de Task 10 se cablean después.

- [ ] **Step 1: Crear `frontend/components/urna/DisclaimerBanner.tsx`.**

```tsx
export default function DisclaimerBanner({ texto }: { texto: string }) {
  return (
    <div style={{
      margin: "0 0 16px", padding: "10px 14px", fontSize: "12px", lineHeight: 1.5,
      borderRadius: "var(--radius-sm)", fontStyle: "italic",
      background: "rgba(255,193,7,0.12)", border: "1px solid rgba(255,193,7,0.4)",
      color: "var(--smata-gold, #b78a00)",
    }}>
      ⚠ {texto}
    </div>
  );
}
```

- [ ] **Step 2: Crear `frontend/components/urna/UrnaParamsBar.tsx`.**

```tsx
"use client";

import { useState } from "react";
import type { UrnaRequest } from "@/lib/urnaApi";

interface Props {
  loading: boolean;
  onRun: (req: UrnaRequest) => void;
}

export default function UrnaParamsBar({ loading, onRun }: Props) {
  const [keywords, setKeywords] = useState("elecciones presidenciales");
  const [country, setCountry] = useState("ar");
  const [date, setDate] = useState("");
  const [csvText, setCsvText] = useState("");
  const [csvName, setCsvName] = useState("");
  const [csvRows, setCsvRows] = useState(0);

  const handleCsv = (file: File | null) => {
    if (!file) { setCsvText(""); setCsvName(""); setCsvRows(0); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      setCsvText(text);
      setCsvName(file.name);
      // filas de datos = líneas no vacías menos el header
      setCsvRows(Math.max(0, text.split(/\r?\n/).filter((l) => l.trim()).length - 1));
    };
    reader.readAsText(file);
  };

  const submit = () => {
    onRun({
      keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
      networks: ["twitter", "instagram", "tiktok"],
      date: date || null,
      country: country.trim().toLowerCase() || "ar",
      pollster_csv: csvText,
    });
  };

  const field: React.CSSProperties = {
    padding: "8px 10px", fontSize: "13px", borderRadius: "var(--radius-sm)",
    border: "1px solid var(--border-color)", background: "var(--bg-secondary)", color: "var(--text-primary)",
  };

  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "center",
      padding: "12px 24px", borderBottom: "1px solid var(--border-color)", background: "var(--bg-secondary)",
    }}>
      <input style={{ ...field, flex: "1 1 260px" }} value={keywords}
             onChange={(e) => setKeywords(e.target.value)} placeholder="Términos (coma-separados)" />
      <input style={{ ...field, width: "70px" }} value={country}
             onChange={(e) => setCountry(e.target.value)} placeholder="país" title="Código ISO (ar, br, ...)" />
      <input style={{ ...field, width: "150px" }} type="date" value={date}
             onChange={(e) => setDate(e.target.value)} title="Desde" />
      <label style={{ ...field, cursor: "pointer", color: "var(--smata-green-light, #4CAF50)" }}>
        ⬆ CSV consultoras
        <input type="file" accept=".csv" style={{ display: "none" }}
               onChange={(e) => handleCsv(e.target.files?.[0] || null)} />
      </label>
      {csvName && <span style={{ fontSize: "12px", color: "var(--text-secondary)" }}>{csvName} · {csvRows} filas</span>}
      <button className="btn" disabled={loading} onClick={submit}
              style={{ background: "var(--smata-green-mid, #2E7D32)", color: "#fff", padding: "8px 16px",
                       fontSize: "13px", opacity: loading ? 0.6 : 1 }}>
        {loading ? "Analizando…" : "Analizar"}
      </button>
    </div>
  );
}
```

- [ ] **Step 3: Crear `frontend/app/boca-de-urna/page.tsx`** (vuelca JSON crudo por ahora; los componentes visuales llegan en Task 10).

```tsx
"use client";

import { useCallback, useState } from "react";
import UrnaParamsBar from "@/components/urna/UrnaParamsBar";
import DisclaimerBanner from "@/components/urna/DisclaimerBanner";
import { runBocaDeUrna, UrnaRequest, UrnaResponse, UrnaStatus } from "@/lib/urnaApi";

const DEFAULT_DISCLAIMER =
  "Este indicador refleja el clima de conversación en redes sociales sobre publicaciones públicas indexadas. No es una muestra representativa del electorado ni una proyección de resultado electoral. Sirve como termómetro direccional, complementario a las encuestas de consultoras.";

export default function BocaDeUrnaPage() {
  const [data, setData] = useState<UrnaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const run = useCallback(async (req: UrnaRequest) => {
    setLoading(true); setError(null); setStatus(null); setData(null);
    try {
      const res = await runBocaDeUrna(req, (s: UrnaStatus) => setStatus(
        s === "waking" ? "El servidor estaba en reposo. Despertándolo… puede tardar ~40s." : "Conectando…"));
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al conectar con el servidor");
    } finally { setLoading(false); setStatus(null); }
  }, []);

  return (
    <main style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 60px)" }}>
      <UrnaParamsBar loading={loading} onRun={run} />
      <div style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
        <DisclaimerBanner texto={data?.meta.disclaimer || DEFAULT_DISCLAIMER} />
        {loading && status && (
          <div style={{ padding: "12px 16px", borderRadius: "var(--radius-sm)",
            background: "rgba(59,130,246,0.1)", border: "1px solid rgba(59,130,246,0.3)", color: "#93C5FD", fontSize: "13px" }}>
            ⏳ {status}
          </div>
        )}
        {error && (
          <div style={{ padding: "12px 16px", borderRadius: "var(--radius-sm)",
            background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#F87171", fontSize: "13px" }}>
            ⚠️ {error}
          </div>
        )}
        {data && <pre style={{ fontSize: "12px", overflow: "auto" }}>{JSON.stringify(data, null, 2)}</pre>}
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Agregar navegación en `frontend/app/layout.tsx`.**

Importar `Link` de `next/link` al tope del archivo:

```tsx
import Link from "next/link";
```

Y dentro del `<div>` derecho del header (el que contiene la fecha y `<ThemeToggle />`, líneas ~53-63), agregar ANTES del bloque de fecha:

```tsx
            <nav style={{ display: "flex", gap: "8px" }}>
              <Link href="/" style={{ fontSize: "12px", color: "rgba(255,255,255,0.85)", textDecoration: "none",
                padding: "4px 10px", borderRadius: "16px", border: "1px solid rgba(255,255,255,0.15)" }}>
                Monitor
              </Link>
              <Link href="/boca-de-urna" style={{ fontSize: "12px", color: "rgba(255,255,255,0.85)", textDecoration: "none",
                padding: "4px 10px", borderRadius: "16px", border: "1px solid rgba(255,255,255,0.15)" }}>
                Boca de Urna
              </Link>
            </nav>
```

- [ ] **Step 5: Verificar build.**

Run (desde `frontend/`): `npm run build`
Expected: build OK, la ruta `/boca-de-urna` aparece en el listado de rutas de Next.

- [ ] **Step 6: Commit.**

```bash
git add frontend/components/urna/DisclaimerBanner.tsx frontend/components/urna/UrnaParamsBar.tsx frontend/app/boca-de-urna/page.tsx frontend/app/layout.tsx
git commit -m "feat(urna): página, navegación, panel de parámetros y disclaimer"
```

---

### Task 10: Componentes visuales (gráfico, evidencia, tabla) y cableado final

Reemplaza el volcado de JSON por el dashboard de dos columnas: gráfico de barras (CSS puro), panel de evidencia, y tabla comparativa con brecha coloreada.

**Files:**
- Create: `frontend/components/urna/SentimentBarChart.tsx`
- Create: `frontend/components/urna/EvidencePanel.tsx`
- Create: `frontend/components/urna/ComparisonTable.tsx`
- Modify: `frontend/app/boca-de-urna/page.tsx` (cablear componentes + warnings + totales)

**Interfaces:**
- Consumes: tipos de `lib/urnaApi.ts`, datos de `UrnaResponse`.

- [ ] **Step 1: Crear `frontend/components/urna/SentimentBarChart.tsx`** (barras horizontales con CSS puro).

```tsx
import type { UrnaCandidato } from "@/lib/urnaApi";

export default function SentimentBarChart({ candidatos }: { candidatos: UrnaCandidato[] }) {
  if (!candidatos.length) return <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>Sin candidatos detectados.</p>;
  const max = Math.max(...candidatos.map((c) => c.pct), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {candidatos.map((c) => (
        <div key={c.nombre} title={`A favor: ${c.pos} · En contra: ${c.neg} · Neutro: ${c.neu} · Menciones: ${c.menciones}`}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "13px", marginBottom: "3px" }}>
            <span style={{ fontWeight: 600 }}>{c.nombre}</span>
            <span style={{ color: "var(--text-secondary)" }}>{c.pct}%</span>
          </div>
          <div style={{ height: "14px", background: "rgba(127,127,127,0.15)", borderRadius: "7px", overflow: "hidden" }}>
            <div style={{ width: `${(c.pct / max) * 100}%`, height: "100%",
              background: "linear-gradient(90deg, var(--smata-green-mid, #2E7D32), var(--smata-green-light, #4CAF50))" }} />
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "2px" }}>
            {c.pos} a favor · {c.neg} en contra · {c.neu} neutro · {c.menciones} menciones
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Crear `frontend/components/urna/EvidencePanel.tsx`.**

```tsx
import type { UrnaEvidencia, Postura } from "@/lib/urnaApi";

const COLOR: Record<Postura, string> = {
  a_favor: "#4CAF50", en_contra: "#F87171", neutro: "#9CA3AF",
};
const LABEL: Record<Postura, string> = {
  a_favor: "a favor", en_contra: "en contra", neutro: "neutro",
};

export default function EvidencePanel({ evidencia }: { evidencia: UrnaEvidencia[] }) {
  if (!evidencia.length) return <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>Sin citas de respaldo.</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {evidencia.map((e, i) => (
        <div key={i} style={{ borderLeft: `3px solid ${COLOR[e.postura]}`, padding: "6px 10px",
          background: "rgba(127,127,127,0.06)", borderRadius: "0 6px 6px 0" }}>
          <div style={{ fontSize: "12px", marginBottom: "4px" }}>
            <span style={{ fontWeight: 600 }}>{e.candidato}</span>
            <span style={{ color: COLOR[e.postura], marginLeft: "6px" }}>· {LABEL[e.postura]}</span>
            <span style={{ color: "var(--text-secondary)", marginLeft: "6px" }}>({e.post.network})</span>
          </div>
          <div style={{ fontSize: "13px", fontStyle: "italic" }}>“{e.cita}”</div>
          {e.post.post_url && (
            <a href={e.post.post_url} target="_blank" rel="noreferrer"
               style={{ fontSize: "11px", color: "var(--smata-green-light, #4CAF50)" }}>
              ver publicación ↗
            </a>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Crear `frontend/components/urna/ComparisonTable.tsx`** (brecha coloreada: verde ≤3pts, ámbar ≤8, rojo > 8).

```tsx
import type { UrnaComparacion } from "@/lib/urnaApi";

function gapColor(gap: number | null): string {
  if (gap === null) return "var(--text-secondary)";
  const a = Math.abs(gap);
  if (a <= 3) return "#4CAF50";
  if (a <= 8) return "#FFC107";
  return "#F87171";
}
const fmt = (n: number | null) => (n === null ? "—" : `${n > 0 ? "+" : ""}${n}`);

export default function ComparisonTable({ comparacion }: { comparacion: UrnaComparacion[] }) {
  if (!comparacion.length) return <p style={{ color: "var(--text-secondary)", fontSize: "13px" }}>Cargá un CSV de consultoras para ver la comparación.</p>;
  const consultoras = Array.from(new Set(comparacion.flatMap((c) => c.consultoras.map((x) => x.consultora)))).sort();
  const th: React.CSSProperties = { textAlign: "left", padding: "6px 8px", fontSize: "11px",
    textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--text-secondary)", borderBottom: "1px solid var(--border-color)" };
  const td: React.CSSProperties = { padding: "6px 8px", fontSize: "13px", borderBottom: "1px solid var(--border-color)" };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", minWidth: "480px" }}>
        <thead>
          <tr>
            <th style={th}>Candidato</th>
            <th style={th}>Redes</th>
            {consultoras.map((c) => <th key={c} style={th}>{c}</th>)}
            <th style={th}>Prom. consult.</th>
            <th style={th}>Brecha prom.</th>
          </tr>
        </thead>
        <tbody>
          {comparacion.map((row) => (
            <tr key={row.candidato}>
              <td style={{ ...td, fontWeight: 600 }}>{row.candidato}</td>
              <td style={td}>{row.redes_pct}%</td>
              {consultoras.map((name) => {
                const cell = row.consultoras.find((x) => x.consultora === name);
                return (
                  <td key={name} style={td}>
                    {cell ? <>{cell.pct}% <span style={{ color: gapColor(cell.gap), fontSize: "11px" }}>({fmt(cell.gap)})</span></> : "—"}
                  </td>
                );
              })}
              <td style={td}>{row.promedio_consultoras === null ? "—" : `${row.promedio_consultoras}%`}</td>
              <td style={{ ...td, color: gapColor(row.gap_promedio), fontWeight: 600 }}>{fmt(row.gap_promedio)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Cablear los componentes en `frontend/app/boca-de-urna/page.tsx`.**

Reemplazar los imports de datos y el bloque `{data && <pre>...</pre>}` por el dashboard. Imports al tope:

```tsx
import SentimentBarChart from "@/components/urna/SentimentBarChart";
import EvidencePanel from "@/components/urna/EvidencePanel";
import ComparisonTable from "@/components/urna/ComparisonTable";
```

Reemplazar `{data && <pre ...>...</pre>}` por:

```tsx
        {data && (
          <>
            {data.meta.warnings.length > 0 && (
              <ul style={{ margin: "0 0 16px", padding: "10px 14px 10px 30px", fontSize: "12px",
                borderRadius: "var(--radius-sm)", background: "rgba(127,127,127,0.08)", color: "var(--text-secondary)" }}>
                {data.meta.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            )}
            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "12px" }}>
              {data.meta.total_posts} publicaciones analizadas · {data.meta.posts_electorales} electorales
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: "24px", alignItems: "start" }}>
              <section style={{ display: "flex", flexDirection: "column", gap: "20px", minWidth: 0 }}>
                <div>
                  <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Sentimiento neto en redes</h3>
                  <SentimentBarChart candidatos={data.candidatos} />
                </div>
                <div>
                  <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Evidencia (citas)</h3>
                  <EvidencePanel evidencia={data.evidencia} />
                </div>
              </section>
              <section style={{ minWidth: 0 }}>
                <h3 style={{ fontSize: "14px", marginBottom: "10px" }}>Redes vs consultoras</h3>
                <ComparisonTable comparacion={data.comparacion} />
              </section>
            </div>
          </>
        )}
```

- [ ] **Step 5: Verificar build.**

Run (desde `frontend/`): `npm run build`
Expected: build OK, sin errores de tipos.

- [ ] **Step 6: Verificación manual en el navegador.**

Levantar backend (`python main.py`) y frontend (`npm run dev`), ir a `/boca-de-urna`, correr un análisis con un CSV de prueba y confirmar: aparecen barras, evidencia con links, tabla con brechas coloreadas, disclaimer y warnings. (Usar la skill `run`/`verify` para automatizar el arranque si está disponible.)

- [ ] **Step 7: Commit.**

```bash
git add frontend/components/urna/SentimentBarChart.tsx frontend/components/urna/EvidencePanel.tsx frontend/components/urna/ComparisonTable.tsx frontend/app/boca-de-urna/page.tsx
git commit -m "feat(urna): dashboard de dos columnas (gráfico, evidencia, tabla)"
```

---

### Task 11: Documentación (README)

Documentar la nueva sección, el endpoint y el formato del CSV.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Agregar en `README.md`** una sección de la Boca de Urna: qué es y su encuadre (termómetro de redes, no encuesta), el endpoint `POST /api/boca-de-urna` con su request/response de ejemplo, el esquema del CSV de consultoras, y una línea en la estructura de archivos mencionando `backend/electoral.py`, `backend/gemini_client.py` y `frontend/app/boca-de-urna/`. Reusar el estilo de tablas/JSON ya presente en el README.

- [ ] **Step 2: Verificar render local del Markdown** (revisar que las tablas y bloques de código cierren bien).

- [ ] **Step 3: Commit.**

```bash
git add README.md
git commit -m "docs(urna): documenta la Boca de Urna, el endpoint y el CSV de consultoras"
```

---

## Self-Review (contra el spec)

**1. Cobertura del spec:**
- §4 decisiones → Tasks: fuente de datos (reuso `fetch_posts`, Task 6), sentimiento neto (Task 4), CSV consultoras (Task 2), auto-detección + canonicalización (Tasks 3-4), brecha actual (Task 5), página nueva (Tasks 9-10), stateless (sin DB en ningún task), `electoral.py` aislado (Tasks 2-6), `gemini_client.py` (Task 1), layout C (Task 10), sin librería de charts (Task 10). ✔
- §6 backend (endpoint, CSV, funciones, fórmula neto, comparación, respuesta, refactor) → Tasks 1-7. ✔
- §6.4 uso de `confianza` (umbral, no pondera, ordena evidencia) → Task 4 (`aggregate_net_sentiment` filtra por `CONF_MIN`; `build_evidence` ordena por confianza). ✔
- §7 frontend (página, componentes, CSS puro, upload CSV, cold-start, tabla con colores) → Tasks 8-10. ✔
- §8 errores/casos borde/testing → Tasks 2-7 (headers 400/503/200, filas salteadas, cero posts, cero candidatos, no reconciliados) + tests en cada task. ✔
- §9 encuadre/disclaimer → Global Constraints + Task 6 (`DISCLAIMER`) + Task 9 (`DisclaimerBanner`) + Task 10 (warnings/totales). ✔
- §10 archivos afectados → coinciden con los Files de los tasks. ✔

**2. Placeholders:** Sin "TBD"/"implementar luego". Todos los pasos con código tienen el código real. La única prosa sin código es Task 11 (docs) y la verificación manual (Task 10 Step 6), que son inherentemente descriptivas. ✔

**3. Consistencia de tipos:** `run_with_rotation` (Task 1) → usado por `analyze_posts_electoral` (Task 3) y mockeado en Task 6. `analyze_posts_electoral` produce items con `candidatos/cita/es_electoral` consumidos por `aggregate_net_sentiment`/`build_evidence` (Task 4). `candidatos` (dicts con `nombre/pct/pos/neg/neu/menciones`) fluye a `compare_vs_pollsters` (Task 5) y al payload (Task 6), y los tipos TS de `lib/urnaApi.ts` (Task 8) reflejan exactamente ese payload, consumido por los componentes (Task 10). `canonical_key` usado consistentemente en Tasks 4-5. ✔
