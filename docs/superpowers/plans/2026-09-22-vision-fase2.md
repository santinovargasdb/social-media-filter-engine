# Fase 2 — `vision.py` (captura → Gemini visión → posts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lector de capturas del scraper local: dada una captura de una red social, extraer y clasificar en UNA llamada a Gemini visión los posts visibles, con la misma semántica direccional que el clasificador de texto.

**Architecture:** Se extiende el transporte compartido `backend/gemini_client.py` para aceptar una imagen opcional (`inline_data`) reutilizando la cascada de modelos y la rotación de API keys tal cual. `scraper_local/vision.py` (nuevo) arma el prompt de visión —importando las reglas de clasificación desde `electoral.py` para que texto y visión clasifiquen igual—, sanea la respuesta y pacea las llamadas bajo el free-tier. CLI incluido para tuning manual en la PC de la oficina.

**Tech Stack:** Python (stdlib + `requests`), pytest con `monkeypatch` (sin red en tests). Playwright solo como herramienta manual del smoke test (NO es dependencia).

**Spec:** `docs/superpowers/specs/2026-09-22-vision-fase2-design.md`

## Global Constraints

- **Sin dependencias nuevas**: backend y scraper usan stdlib + `requests`. Nada se agrega a `requirements.txt`.
- **Suite existente verde**: desde `backend/`: `.venv\Scripts\python.exe -m pytest -q` termina en 0 fails. Los llamadores de texto de `gemini_client` (normalizer/electoral) NO cambian de comportamiento.
- **Enum de posturas exacto**: `("a_favor", "en_contra", "neutro")` — es `electoral.POSTURAS_VALIDAS`, no redefinirlo.
- **Regla de encuestas balanceada VERBATIM**: punteros/competitivos → `a_favor`, marginales → `en_contra`, intermedios → `neutro`. NO endurecerla a "podio estricto".
- **No tocar** frontend, textos institucionales (disclaimer), ni `main.py`.
- Comentarios y mensajes en castellano rioplatense, como el resto del repo.
- Los tests nuevos de `scraper_local` se corren desde la raíz del repo: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`.

## File Structure

- `backend/gemini_client.py` (modificar) — parámetro opcional `image=(data_b64, mime)` en toda la cadena `generate_raw → call_gemini_json → run_cascade → run_with_rotation`. Default `None` = payload de texto idéntico al actual.
- `backend/test_gemini_client.py` (modificar) — fakes existentes aceptan `image=None`; 3 tests nuevos de visión.
- `backend/electoral.py` (modificar) — extraer el bloque de reglas de candidatos del prompt a la constante `REGLAS_CANDIDATOS` (texto VERBATIM; el prompt resultante no cambia ni en un carácter).
- `scraper_local/vision.py` (crear) — `_load_image`, `_build_vision_prompt`, `_sanitize_posts`, `read_capture`, `_pace`, CLI `main`.
- `scraper_local/test_vision.py` (crear) — unit tests con Gemini mockeado.
- `scraper_local/testdata/feed_falso.html` (crear) — feed estilo X con contenido conocido para el smoke test real.
- `scraper_local/testdata/README.md` (crear) — resultados esperados + cómo correr el smoke test.

---

### Task 1: Transporte de visión en `gemini_client.py`

**Files:**
- Modify: `backend/gemini_client.py`
- Test: `backend/test_gemini_client.py`

**Interfaces:**
- Consumes: nada de otras tareas.
- Produces: `gemini_client.run_with_rotation(prompt: str, image: tuple[str, str] | None = None) -> tuple[list | None, int | None]` donde `image = (data_base64, mime_type)`. Mismo contrato de retorno que hoy. También `generate_raw(model, api_key, prompt, timeout=45, image=None)`, `call_gemini_json(model, api_key, prompt, image=None)`, `run_cascade(prompt, api_key, image=None)`.

- [ ] **Step 1: Actualizar los fakes existentes y escribir los tests nuevos (fallan)**

En `backend/test_gemini_client.py`, actualizar la firma de los fakes existentes para tolerar el parámetro nuevo (el comportamiento que verifican no cambia):

- en `test_run_cascade_primer_modelo_ok`, `test_run_cascade_fallback_en_429` y `test_run_cascade_error_no_reintentable_corta`: `def fake(model, api_key, prompt):` → `def fake(model, api_key, prompt, image=None):`
- en `test_run_with_rotation_rota_a_secundaria_en_429`: `def fake_cascade(prompt, api_key):` → `def fake_cascade(prompt, api_key, image=None):`

Y AGREGAR al final del archivo:

```python
class _FakeResp:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}


def test_generate_raw_texto_no_manda_inline_data(monkeypatch):
    """Sin imagen, el payload es EXACTAMENTE el de siempre (una sola part de texto)."""
    capturado = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        capturado["payload"] = json
        return _FakeResp()
    monkeypatch.setattr(gc.requests, "post", fake_post)
    raw, status = gc.generate_raw("m", "k", "hola")
    assert raw == "[]" and status is None
    assert capturado["payload"]["contents"][0]["parts"] == [{"text": "hola"}]


def test_generate_raw_vision_manda_inline_data(monkeypatch):
    """Con imagen, el payload lleva la part de texto + inline_data con mime y base64."""
    capturado = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        capturado["payload"] = json
        return _FakeResp()
    monkeypatch.setattr(gc.requests, "post", fake_post)
    raw, _ = gc.generate_raw("m", "k", "lee la captura", image=("QUJD", "image/png"))
    assert raw == "[]"
    parts = capturado["payload"]["contents"][0]["parts"]
    assert parts[0] == {"text": "lee la captura"}
    assert parts[1] == {"inline_data": {"mime_type": "image/png", "data": "QUJD"}}


def test_cascada_y_rotacion_pasan_la_imagen(monkeypatch):
    """run_with_rotation → run_cascade → call_gemini_json propagan la imagen intacta."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GEMINI_API_KEY_SECONDARY", raising=False)
    imagenes = []
    def fake(model, api_key, prompt, image=None):
        imagenes.append(image)
        return ([{"ok": True}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, _ = gc.run_with_rotation("p", image=("DATA", "image/webp"))
    assert parsed == [{"ok": True}]
    assert imagenes == [("DATA", "image/webp")]
```

- [ ] **Step 2: Correr los tests nuevos y verificar que fallan**

Desde `backend/`: `.venv\Scripts\python.exe -m pytest test_gemini_client.py -v`
Expected: los 3 nuevos FAILN con `TypeError: generate_raw() got an unexpected keyword argument 'image'` (o equivalente); los 5 viejos PASAN.

- [ ] **Step 3: Implementar el parámetro `image` en la cadena**

En `backend/gemini_client.py`:

`generate_raw` — reemplazar la firma y el armado del payload (el resto del cuerpo queda igual):

```python
def generate_raw(model: str, api_key: str, prompt: str, timeout: int = 45,
                 image: tuple[str, str] | None = None) -> tuple[str | None, int | None]:
    """Llamada cruda a un modelo Gemini. Devuelve (texto_sin_fences, http_status_si_error).
    - (texto, None): éxito       - (None, status): error HTTP       - (None, None): error de red.
    `image`: (data_base64, mime_type) opcional — agrega la imagen al prompt (visión)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    parts: list[dict] = [{"text": prompt}]
    if image is not None:
        data_b64, mime = image
        parts.append({"inline_data": {"mime_type": mime, "data": data_b64}})
    payload = {"contents": [{"parts": parts}]}
```

`call_gemini_json` — firma `def call_gemini_json(model: str, api_key: str, prompt: str, image: tuple[str, str] | None = None)` y la llamada interna pasa a `generate_raw(model, api_key, prompt, timeout=45, image=image)`.

`run_cascade` — firma `def run_cascade(prompt: str, api_key: str, image: tuple[str, str] | None = None)` y la llamada interna pasa a `call_gemini_json(model, api_key, prompt, image=image)`.

`run_with_rotation` — firma `def run_with_rotation(prompt: str, image: tuple[str, str] | None = None)` y las DOS llamadas internas pasan a `run_cascade(prompt, api_key, image=image)` / `run_cascade(prompt, secondary_key, image=image)`.

Actualizar el docstring del módulo (línea "No conoce el dominio...") agregando: "Acepta una imagen opcional (visión) sin cambiar el contrato de texto."

- [ ] **Step 4: Correr TODA la suite del backend y verificar verde**

Desde `backend/`: `.venv\Scripts\python.exe -m pytest -q`
Expected: todos PASAN (los mocks `lambda prompt:` de `test_electoral.py` siguen funcionando porque `electoral` llama `run_with_rotation(prompt)` con un solo posicional).

- [ ] **Step 5: Commit**

```bash
git add backend/gemini_client.py backend/test_gemini_client.py
git commit -m "feat(gemini): imagen opcional (inline_data) en el transporte — visión con la misma cascada y rotación"
```

---

### Task 2: `vision.py` — carga de imagen, prompt compartido y saneo

**Files:**
- Modify: `backend/electoral.py` (extraer `REGLAS_CANDIDATOS`, ~líneas 194-223)
- Create: `scraper_local/vision.py`
- Test: `scraper_local/test_vision.py` (crear), `backend/test_electoral.py` (solo correr, no tocar)

**Interfaces:**
- Consumes: `gemini_client.run_with_rotation(prompt, image=(b64, mime))` (Task 1).
- Produces:
  - `electoral.REGLAS_CANDIDATOS: str` — bloque verbatim de reglas de candidatos, usado por ambos prompts.
  - `vision.read_capture(image, red: str, candidatos: list[str] | None = None, mime: str | None = None) -> list[dict] | None` — `None` = upstream falló (mismo contrato que electoral), `[]` = sin posts. Cada post: `{"texto", "autor", "fecha", "red", "es_electoral", "candidatos": [{"nombre", "postura", "confianza"}], "cita"}`.
  - `vision._load_image(image, mime=None) -> tuple[str, str]`, `vision._build_vision_prompt(red, candidatos) -> str`, `vision._sanitize_posts(parsed, red) -> list[dict]`.
  - En esta task `read_capture` NO pacea todavía (el paceo es Task 3).

- [ ] **Step 1: Extraer `REGLAS_CANDIDATOS` en `electoral.py`**

Agregar después de `POSTURAS_VALIDAS` (línea ~32):

```python
# Reglas de clasificación de candidatos COMPARTIDAS entre el prompt de texto
# (_build_electoral_prompt) y el prompt de visión (scraper_local/vision.py), para
# que ambos clasifiquen con la MISMA semántica direccional. Texto verbatim del
# prompt original — cambiarlo acá cambia los dos.
REGLAS_CANDIDATOS = """   - "nombre": el nombre COMPLETO y CANÓNICO del candidato (ej. si dice "Milei" o "el León", devolvé "Javier Milei"). Unificá alias y apodos al nombre canónico.
   - "postura": la SEÑAL DIRECCIONAL del posteo hacia ese candidato (no solo la opinión explícita del autor: también la ventaja/desventaja que el posteo le atribuye). Exactamente uno de:
       * "a_favor": lo muestra FAVORABLE o EN VENTAJA — lo elogia/apoya/respalda, O reporta que lidera, puntea o tiene una intención de voto ALTA en una encuesta, O que gana/ganaría.
       * "en_contra": lo muestra DESFAVORABLE o EN DESVENTAJA — lo critica/ataca, O reporta que tiene una intención de voto MARGINAL o muy baja (claramente relegado) en una encuesta, O que pierde/perdería.
       * "neutro": mención meramente informativa, sin señal direccional clara, o con una intención de voto intermedia que no lo distingue.
   - "confianza": número entre 0 y 1 con tu certeza sobre esa señal.
   Si no hay candidatos, devolvé [].
   REGLA DE ENCUESTAS/SONDEOS: si la publicación es una encuesta o sondeo que reporta porcentajes de intención de voto, USÁ los porcentajes como señal (no lo trates como neutro por ser el autor imparcial): el/los candidato(s) puntero(s) o competitivo(s), con intención de voto claramente alta → "a_favor"; los de intención de voto marginal o muy baja (claramente fuera de la pelea) → "en_contra"; los intermedios → "neutro". NO devuelvas como candidatos las opciones que no son personas (voto en blanco, impugnado, indeciso, "no sabe / no contesta", "ninguno")."""
```

**IMPORTANTE:** el texto de la constante debe ser byte a byte el que hoy vive en `_build_electoral_prompt` (líneas "- \"nombre\": ..." hasta "...\"ninguno\")."). Copiarlo del archivo, no retipearlo.

En `_build_electoral_prompt`, reemplazar ese bloque (desde la línea `   - "nombre": ...` hasta la línea `   REGLA DE ENCUESTAS/SONDEOS: ... "ninguno").` inclusive) por la interpolación:

```python
2. "candidatos": lista de los candidatos presidenciales mencionados. Por cada uno:
{REGLAS_CANDIDATOS}
3. "cita": el fragmento textual breve del posteo que justifica la señal (o "" si no aplica).
```

- [ ] **Step 2: Verificar que el prompt de texto no cambió**

Desde `backend/`: `.venv\Scripts\python.exe -m pytest -q`
Expected: TODO verde (si algún test de electoral falla, el texto extraído no es verbatim — corregir la constante, no el test).

- [ ] **Step 3: Escribir los tests de `vision.py` (fallan)**

Crear `scraper_local/test_vision.py`:

```python
"""Unit tests de vision.py — Gemini SIEMPRE mockeado (sin red, sin API key)."""
import base64
import json

import vision


PARSED_OK = [
    {"autor": "@ok", "fecha": "2 h", "texto": "Gran discurso de Milei",
     "es_electoral": True,
     "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 1.7}],
     "cita": "Gran discurso"},
    {"autor": "@vacio", "fecha": "", "texto": "   ",
     "es_electoral": False, "candidatos": [], "cita": ""},
    {"autor": "@raro", "fecha": "1 d", "texto": "posturas inválidas",
     "es_electoral": True,
     "candidatos": [{"nombre": "Javier Milei", "postura": "me_gusta", "confianza": 0.9},
                    {"nombre": "", "postura": "a_favor", "confianza": 0.9},
                    {"nombre": "Axel Kicillof", "postura": "EN_CONTRA", "confianza": "alta"}],
     "cita": ""},
]


def _mock_gemini(monkeypatch, respuesta=(PARSED_OK, None)):
    visto = {}
    def fake(prompt, image=None):
        visto["prompt"] = prompt
        visto["image"] = image
        return respuesta
    monkeypatch.setattr(vision.gemini_client, "run_with_rotation", fake)
    return visto


def test_read_capture_sanea_y_completa(monkeypatch, tmp_path):
    """Camino feliz: manda la imagen en base64, sanea posturas/confianza y agrega la red."""
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"fake-png")
    visto = _mock_gemini(monkeypatch)

    posts = vision.read_capture(cap, "twitter")

    assert visto["image"] == (base64.b64encode(b"fake-png").decode("ascii"), "image/png")
    # El post sin texto se descarta; quedan 2.
    assert [p["texto"] for p in posts] == ["Gran discurso de Milei", "posturas inválidas"]
    p0 = posts[0]
    assert p0["red"] == "twitter" and p0["autor"] == "@ok" and p0["fecha"] == "2 h"
    assert p0["es_electoral"] is True and p0["cita"] == "Gran discurso"
    # Confianza 1.7 clampeada a 1.0.
    assert p0["candidatos"] == [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 1.0}]
    # Postura inválida y nombre vacío descartados; "EN_CONTRA" normaliza; "alta" -> 0.0.
    assert posts[1]["candidatos"] == [{"nombre": "Axel Kicillof", "postura": "en_contra", "confianza": 0.0}]


def test_read_capture_upstream_devuelve_none(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    _mock_gemini(monkeypatch, respuesta=(None, 503))
    assert vision.read_capture(cap, "twitter") is None


def test_read_capture_acepta_bytes_y_jpg(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    visto = _mock_gemini(monkeypatch, respuesta=([], None))
    assert vision.read_capture(b"crudo", "twitter") == []
    assert visto["image"][1] == "image/png"  # bytes sin mime -> png por default
    cap = tmp_path / "cap.jpg"
    cap.write_bytes(b"jpg")
    vision.read_capture(cap, "twitter")
    assert visto["image"][1] == "image/jpeg"


def test_prompt_incluye_reglas_red_y_candidatos(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    visto = _mock_gemini(monkeypatch, respuesta=([], None))
    vision.read_capture(cap, "twitter")
    prompt = visto["prompt"]
    # Reglas compartidas con el clasificador de texto (una sola fuente de verdad).
    assert "REGLA DE ENCUESTAS/SONDEOS" in prompt
    assert '"a_favor"' in prompt and '"en_contra"' in prompt and '"neutro"' in prompt
    assert '"twitter"' in prompt
    assert "Javier Milei" in prompt  # CANDIDATOS_DEFAULT como referencia
    assert "Promocionado" in prompt  # regla de ignorar publicidad
    # Con lista propia, usa esa lista.
    vision.read_capture(cap, "twitter", candidatos=["Fulano de Tal"])
    assert "Fulano de Tal" in visto["prompt"]


def test_sanitize_descarta_items_no_dict():
    assert vision._sanitize_posts(["texto suelto", 42, {"texto": "ok", "candidatos": []}], "x") == [
        {"texto": "ok", "autor": "", "fecha": "", "red": "x",
         "es_electoral": False, "candidatos": [], "cita": ""}]
```

- [ ] **Step 4: Correr los tests y verificar que fallan**

Desde la raíz del repo: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: FAIL con `ModuleNotFoundError: No module named 'vision'`.

- [ ] **Step 5: Implementar `scraper_local/vision.py`**

```python
"""
Fase 2 del scraper local — lector de capturas por visión.

Manda UNA captura de pantalla a Gemini (visión, transporte compartido del backend:
misma cascada de modelos y rotación de keys) con un prompt que extrae Y clasifica
los posts visibles en una sola pasada, con las MISMAS reglas direccionales que el
clasificador de texto (importa electoral.REGLAS_CANDIDATOS). Devuelve posts
saneados listos para agregar (Fase 3: dedup + aggregate + snapshot).

Uso como CLI (tuning en la PC de la oficina):
    python vision.py captura.png --red twitter [--candidatos "A,B"]

Paceo free-tier: VISION_MIN_INTERVAL segundos entre llamadas (default 10; 0 = sin
espera, para tests o API paga).
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

# El scraper corre desde el repo clonado en la PC de la oficina: reusa el código
# del backend agregándolo al path (mismo patrón que usará run.py en Fase 3).
BACKEND_DIR = str(Path(__file__).resolve().parent.parent / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import gemini_client  # noqa: E402
from electoral import CANDIDATOS_DEFAULT, POSTURAS_VALIDAS, REGLAS_CANDIDATOS, canonical_key  # noqa: E402

_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# Reloj monótono de la última llamada a Gemini (el batch es secuencial).
_last_call = 0.0


def _load_image(image, mime: str | None = None) -> tuple[str, str]:
    """`image` es una ruta (str/Path) o bytes. Devuelve (base64, mime_type)."""
    if isinstance(image, (str, Path)):
        path = Path(image)
        data = path.read_bytes()
        mime = mime or _MIMES.get(path.suffix.lower(), "image/png")
    else:
        data = bytes(image)
        mime = mime or "image/png"
    return base64.b64encode(data).decode("ascii"), mime


def _build_vision_prompt(red: str, candidatos: list[str]) -> str:
    lista = ", ".join(candidatos)
    return f"""Sos un analista de opinión pública que evalúa publicaciones de redes sociales del ámbito argentino de cara a las próximas elecciones presidenciales.

Vas a recibir UNA CAPTURA DE PANTALLA de la red social "{red}". Tu tarea es EXTRAER cada publicación visible y clasificarla, en una sola pasada.

REGLAS DE LECTURA DE PANTALLA (OBLIGATORIAS):
- Extraé SOLO las publicaciones COMPLETAMENTE visibles. Si una publicación está cortada por un borde de la captura, ignorala.
- Ignorá la interfaz de la red: menús, buscadores, tendencias, sugerencias ("a quién seguir"), contadores de interacción y publicidad (todo lo marcado "Promocionado" o "Ad").
- NO inventes NADA. "autor": el @usuario visible (si no se ve un @, el nombre mostrado). "fecha": el texto de fecha TAL CUAL aparece en pantalla (ej. "2 h", "12 sep."). Si un dato no se ve, dejá "".
- "texto": el texto completo de la publicación tal como se lee en la captura.
- REGLA DE AISLAMIENTO: evaluá cada publicación de forma totalmente AISLADA e INDEPENDIENTE de las demás.

Por cada publicación determiná:
1. "es_electoral": true solo si la publicación habla de candidatos, partidos o la contienda electoral presidencial argentina; false si es ruido, spam u otro tema.
2. "candidatos": lista de los candidatos presidenciales mencionados. Por cada uno:
{REGLAS_CANDIDATOS}
3. "cita": el fragmento textual breve de la publicación que justifica la señal (o "" si no aplica).

Candidatos de referencia (lista NO exhaustiva; puede aparecer alguno que no esté acá): {lista}.

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código): una LISTA con un objeto por publicación visible, en el orden en que aparecen. Formato exacto:
[
  {{ "autor": "@usuario", "fecha": "2 h", "texto": "...", "es_electoral": true, "candidatos": [{{ "nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9 }}], "cita": "..." }}
]
Si no hay publicaciones legibles, devolvé []."""


def _sanitize_posts(parsed: list, red: str) -> list[dict]:
    """Sanea la respuesta de Gemini (mismo espíritu que electoral._analyze_electoral_batch):
    posts sin texto afuera, posturas fuera del enum afuera, confianza clampeada a [0,1]."""
    out: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        texto = (item.get("texto") or "").strip()
        if not texto:
            continue
        candidatos_saneados = []
        for c in item.get("candidatos", []) or []:
            if not isinstance(c, dict):
                continue
            nombre = (c.get("nombre") or "").strip()
            postura = (c.get("postura") or "").strip().lower()
            if not nombre or not canonical_key(nombre) or postura not in POSTURAS_VALIDAS:
                continue
            try:
                conf = float(c.get("confianza", 0))
            except (TypeError, ValueError):
                conf = 0.0
            conf = min(max(conf, 0.0), 1.0)
            candidatos_saneados.append({"nombre": nombre, "postura": postura, "confianza": conf})
        out.append({
            "texto": texto,
            "autor": (item.get("autor") or "").strip(),
            "fecha": (item.get("fecha") or "").strip(),
            "red": red,
            "es_electoral": bool(item.get("es_electoral", False)),
            "candidatos": candidatos_saneados,
            "cita": (item.get("cita") or "").strip(),
        })
    return out


def read_capture(image, red: str, candidatos: list[str] | None = None,
                 mime: str | None = None) -> list[dict] | None:
    """Lee UNA captura con Gemini visión. Devuelve los posts saneados, [] si no se
    vio ninguno, o None si el transporte falló (mismo contrato que electoral)."""
    data_b64, mime = _load_image(image, mime)
    prompt = _build_vision_prompt(red, candidatos or CANDIDATOS_DEFAULT)
    _pace()
    parsed, _status = gemini_client.run_with_rotation(prompt, image=(data_b64, mime))
    if parsed is None:
        print("ERROR vision: Gemini no devolvió resultado (upstream).")
        return None
    return _sanitize_posts(parsed, red)


def _pace() -> None:
    """Espera lo que falte del intervalo mínimo entre llamadas (free-tier)."""
    global _last_call
    try:
        interval = float(os.environ.get("VISION_MIN_INTERVAL", "10"))
    except ValueError:
        interval = 10.0
    now = time.monotonic()
    if interval > 0 and _last_call > 0:
        restante = interval - (now - _last_call)
        if restante > 0:
            time.sleep(restante)
    _last_call = time.monotonic()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Lee una captura de una red social con Gemini visión y devuelve los posts en JSON.")
    ap.add_argument("captura", help="ruta de la imagen (png/jpg/webp)")
    ap.add_argument("--red", default="twitter", help="red de la captura (default: twitter)")
    ap.add_argument("--candidatos", default="",
                    help="lista separada por comas (default: CANDIDATOS_DEFAULT del backend)")
    args = ap.parse_args(argv)
    candidatos = [c.strip() for c in args.candidatos.split(",") if c.strip()] or None
    posts = read_capture(args.captura, args.red, candidatos)
    if posts is None:
        print("ERROR: Gemini no respondió (¿GEMINI_API_KEY configurada? ¿cuota?).", file=sys.stderr)
        return 1
    print(json.dumps(posts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

(Esta task incluye `_pace()` porque `read_capture` lo llama; los TESTS de paceo y CLI son Task 3.)

- [ ] **Step 6: Correr los tests de vision y verificar que pasan**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: 5 PASAN.

- [ ] **Step 7: Correr la suite del backend (electoral no se rompió)**

Desde `backend/`: `.venv\Scripts\python.exe -m pytest -q`
Expected: todo verde.

- [ ] **Step 8: Commit**

```bash
git add backend/electoral.py scraper_local/vision.py scraper_local/test_vision.py
git commit -m "feat(vision): lector de capturas con Gemini visión — reglas compartidas con el clasificador de texto"
```

---

### Task 3: Paceo free-tier + CLI

**Files:**
- Modify: `scraper_local/vision.py` (ya tiene `_pace` y `main` de Task 2 — esta task los TESTEA y corrige lo que falle)
- Test: `scraper_local/test_vision.py`

**Interfaces:**
- Consumes: `vision._pace()`, `vision.main(argv)`, `vision.read_capture` (Task 2).
- Produces: comportamiento verificado del paceo (`VISION_MIN_INTERVAL`, default 10, 0 = sin espera) y del CLI (`main(argv) -> int`, JSON por stdout, 1 si upstream falla).

- [ ] **Step 1: Escribir los tests de paceo y CLI (fallan si hay bugs)**

AGREGAR a `scraper_local/test_vision.py`:

```python
def test_pace_espera_lo_que_falta(monkeypatch):
    """Si pasaron 4s de un intervalo de 10, duerme los 6 restantes."""
    monkeypatch.setenv("VISION_MIN_INTERVAL", "10")
    monkeypatch.setattr(vision, "_last_call", 100.0)
    tiempos = iter([104.0, 110.0])
    dormido = []
    monkeypatch.setattr(vision.time, "monotonic", lambda: next(tiempos))
    monkeypatch.setattr(vision.time, "sleep", lambda s: dormido.append(s))
    vision._pace()
    assert dormido == [6.0]
    assert vision._last_call == 110.0


def test_pace_intervalo_cumplido_no_espera(monkeypatch):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "10")
    monkeypatch.setattr(vision, "_last_call", 100.0)
    monkeypatch.setattr(vision.time, "monotonic", lambda: 250.0)
    monkeypatch.setattr(vision.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("no debía dormir")))
    vision._pace()


def test_pace_cero_desactiva_y_primera_llamada_no_espera(monkeypatch):
    monkeypatch.setattr(vision.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("no debía dormir")))
    monkeypatch.setattr(vision.time, "monotonic", lambda: 300.0)
    # VISION_MIN_INTERVAL=0 -> nunca espera, aunque la última llamada sea reciente.
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    monkeypatch.setattr(vision, "_last_call", 299.0)
    vision._pace()
    # Primera llamada (_last_call == 0) -> no espera aunque haya intervalo.
    monkeypatch.setenv("VISION_MIN_INTERVAL", "10")
    monkeypatch.setattr(vision, "_last_call", 0.0)
    vision._pace()


def test_cli_imprime_json(monkeypatch, capsys, tmp_path):
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    monkeypatch.setattr(vision, "read_capture",
                        lambda image, red, candidatos=None, mime=None: [{"texto": "hola", "red": red}])
    rc = vision.main([str(cap), "--red", "twitter"])
    assert rc == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida == [{"texto": "hola", "red": "twitter"}]


def test_cli_pasa_candidatos_y_falla_con_upstream(monkeypatch, capsys, tmp_path):
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    visto = {}
    def fake_read(image, red, candidatos=None, mime=None):
        visto["candidatos"] = candidatos
        return None
    monkeypatch.setattr(vision, "read_capture", fake_read)
    rc = vision.main([str(cap), "--candidatos", "Juan Pérez, Ana López"])
    assert rc == 1
    assert visto["candidatos"] == ["Juan Pérez", "Ana López"]
    assert "no respondió" in capsys.readouterr().err
```

- [ ] **Step 2: Correr los tests**

Desde la raíz: `backend\.venv\Scripts\python.exe -m pytest -q scraper_local`
Expected: 10 PASAN. Si alguno de paceo/CLI falla, corregir `vision.py` (no el test) — el comportamiento esperado es el de los tests.

- [ ] **Step 3: Commit**

```bash
git add scraper_local/test_vision.py scraper_local/vision.py
git commit -m "test(vision): paceo free-tier y CLI cubiertos con mocks"
```

---

### Task 4: Fixture de feed falso + smoke test real (manual, lo corre el orquestador)

**Files:**
- Create: `scraper_local/testdata/feed_falso.html`
- Create: `scraper_local/testdata/README.md`
- Create (generada): `scraper_local/testdata/feed_falso.png` (captura del HTML)

**Interfaces:**
- Consumes: el CLI `python vision.py testdata\feed_falso.png --red twitter` (Task 3) con `GEMINI_API_KEY` real.
- Produces: fixture reproducible + resultados esperados documentados para re-probar el prompt cuando cambie.

- [ ] **Step 1: Crear `scraper_local/testdata/feed_falso.html`**

Feed estilo X con contenido INVENTADO y conocido: 5 posts completos (elogio, crítica, encuesta, neutro, ruido), 1 publicidad marcada "Promocionado" (debe ignorarse), un sidebar "A quién seguir" (debe ignorarse) y un post cortado al fondo (debe ignorarse):

```html
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Feed falso para smoke test de vision.py — contenido inventado</title>
<style>
  body { margin:0; background:#000; color:#e7e9ea; font-family:"Segoe UI",Arial,sans-serif; display:flex; justify-content:center; }
  .layout { display:flex; gap:24px; }
  .feed { width:600px; border-left:1px solid #2f3336; border-right:1px solid #2f3336;
          height:1360px; overflow:hidden; }
  .post { padding:14px 16px; border-bottom:1px solid #2f3336; }
  .head { display:flex; gap:6px; align-items:center; font-size:15px; }
  .nombre { font-weight:700; }
  .handle, .fecha, .dot { color:#71767b; }
  .texto { margin-top:4px; font-size:15px; line-height:1.35; }
  .acciones { margin-top:10px; color:#71767b; font-size:13px; display:flex; gap:56px; }
  .promo { color:#71767b; font-size:12px; margin-top:6px; }
  .sidebar { width:300px; padding-top:12px; }
  .caja { background:#16181c; border-radius:16px; padding:14px; }
  .caja h3 { margin:0 0 10px; font-size:19px; }
  .sugerido { display:flex; justify-content:space-between; padding:8px 0; font-size:14px; }
  .btn { background:#eff3f4; color:#0f1419; border-radius:999px; padding:5px 14px; font-weight:700; font-size:13px; }
</style>
</head>
<body>
<div class="layout">
  <div class="feed">
    <div class="post">
      <div class="head"><span class="nombre">La Jefa del Barrio</span><span class="handle">@LaJefaDelBarrio</span><span class="dot">·</span><span class="fecha">2 h</span></div>
      <div class="texto">El León volvió a rugir. Milei dio anoche el mejor discurso de su carrera: por primera vez en años siento que hay un rumbo claro para el país. VLLC 🦁</div>
      <div class="acciones"><span>💬 48</span><span>🔁 210</span><span>❤️ 1.204</span></div>
    </div>
    <div class="post">
      <div class="head"><span class="nombre">Política Federal</span><span class="handle">@PoliticaFederal</span><span class="dot">·</span><span class="fecha">4 h</span></div>
      <div class="texto">Kicillof volvió a esquivar las preguntas sobre la deuda de la provincia. No se puede gobernar escondiéndose de los periodistas: la gente merece respuestas, no excusas.</div>
      <div class="acciones"><span>💬 132</span><span>🔁 89</span><span>❤️ 456</span></div>
    </div>
    <div class="post">
      <div class="head"><span class="nombre">Tienda Ofertas</span><span class="handle">@TiendaOfertasAR</span><span class="dot">·</span><span class="fecha"></span></div>
      <div class="texto">🔥 HOT SALE: 3x2 en zapatillas deportivas y envío gratis a todo el país. ¡Solo por hoy!</div>
      <div class="promo">Promocionado</div>
      <div class="acciones"><span>💬 2</span><span>🔁 1</span><span>❤️ 15</span></div>
    </div>
    <div class="post">
      <div class="head"><span class="nombre">Data Electoral AR</span><span class="handle">@DataElectoralAR</span><span class="dot">·</span><span class="fecha">5 h</span></div>
      <div class="texto">🗳️ Nueva encuesta presidencial (Consultora Delta, septiembre 2026): Milei 38%, Kicillof 34%, Manes 3%, indecisos 12%. La polarización sigue firme de cara a 2027.</div>
      <div class="acciones"><span>💬 310</span><span>🔁 502</span><span>❤️ 891</span></div>
    </div>
    <div class="post">
      <div class="head"><span class="nombre">Agenda Congreso</span><span class="handle">@AgendaCongreso</span><span class="dot">·</span><span class="fecha">6 h</span></div>
      <div class="texto">Massa presentó esta tarde su nuevo libro en el centro cultural de Tigre. Estuvieron presentes varios intendentes del conurbano y dirigentes del Frente Renovador.</div>
      <div class="acciones"><span>💬 21</span><span>🔁 14</span><span>❤️ 98</span></div>
    </div>
    <div class="post">
      <div class="head"><span class="nombre">Fanático del Fóbal</span><span class="handle">@FanaticoDelFobal</span><span class="dot">·</span><span class="fecha">7 h</span></div>
      <div class="texto">Qué partidazo se comió River anoche, pero el 9 no puede errar esas dos de cabeza solo abajo del arco 😤⚽ Así no llegamos a nada en la Libertadores.</div>
      <div class="acciones"><span>💬 76</span><span>🔁 12</span><span>❤️ 340</span></div>
    </div>
    <div class="post">
      <div class="head"><span class="nombre">Cortado Por El Borde</span><span class="handle">@PostCortado</span><span class="dot">·</span><span class="fecha">8 h</span></div>
      <div class="texto">Este post está cortado por el borde inferior de la captura y NO debería aparecer en la extracción porque no se lee completo. Bullrich anunció que</div>
    </div>
  </div>
  <div class="sidebar">
    <div class="caja">
      <h3>A quién seguir</h3>
      <div class="sugerido"><span>Noticias Ya <span class="handle">@NoticiasYaAR</span></span><span class="btn">Seguir</span></div>
      <div class="sugerido"><span>Clima BsAs <span class="handle">@ClimaBsAs</span></span><span class="btn">Seguir</span></div>
    </div>
  </div>
</div>
</body>
</html>
```

(La altura fija `1360px` con `overflow:hidden` corta el último post a la mitad — eso es intencional.)

- [ ] **Step 2: Crear `scraper_local/testdata/README.md`**

```markdown
# Testdata — smoke test real de `vision.py`

`feed_falso.html` es un feed estilo X con contenido **inventado y conocido**, para
verificar el prompt de visión contra Gemini REAL (el unit test usa mocks; esto
prueba la extracción de verdad). La captura `feed_falso.png` se genera
screenshoteando el HTML (Playwright o cualquier navegador, viewport ~950×1400).

## Cómo correrlo

Desde la raíz del repo (PowerShell), con la API key del backend:

    Get-Content backend\.env | ForEach-Object {
      if ($_ -match '^([^=#]+)=(.*)$') { Set-Item "env:$($matches[1].Trim())" $matches[2].Trim() } }
    $env:VISION_MIN_INTERVAL = "0"
    backend\.venv\Scripts\python.exe scraper_local\vision.py scraper_local\testdata\feed_falso.png --red twitter

## Resultado esperado

| # | autor | es_electoral | candidatos (postura) |
|---|-------|--------------|----------------------|
| 1 | @LaJefaDelBarrio | true | Javier Milei (a_favor) |
| 2 | @PoliticaFederal | true | Axel Kicillof (en_contra) |
| 3 | @DataElectoralAR | true | Javier Milei (a_favor), Axel Kicillof (a_favor), Facundo Manes (en_contra) — regla de encuestas: punteros a_favor, marginal en_contra; "indecisos" NO es candidato |
| 4 | @AgendaCongreso | true | Sergio Massa (neutro) |
| 5 | @FanaticoDelFobal | false | — |

**NO deben aparecer:** el post "Promocionado" (@TiendaOfertasAR), el sidebar
"A quién seguir", ni el post cortado (@PostCortado).

Criterio de aprobación: los 5 posts de la tabla extraídos con su autor y postura;
cero posts prohibidos. La fecha puede variar en formato ("2 h" vs "2h") — no es
criterio. Si Gemini falla una postura, ajustar el PROMPT en `vision.py` (no la
regla compartida `REGLAS_CANDIDATOS`, que es del clasificador de texto también) y
re-correr.
```

- [ ] **Step 3: Generar la captura**

El orquestador screenshotea `feed_falso.html` (viewport ≈950×1400, fondo negro completo) y guarda `scraper_local/testdata/feed_falso.png`. Con Playwright instalado sería:

```
npx playwright screenshot --viewport-size=950,1400 "file:///<ruta>/scraper_local/testdata/feed_falso.html" scraper_local/testdata/feed_falso.png
```

(o con las herramientas de browser del orquestador — cualquier método que produzca un PNG fiel sirve).

- [ ] **Step 4: Correr el smoke test real y verificar contra la tabla**

Correr los comandos del README (API key real, `VISION_MIN_INTERVAL=0`).
Expected: JSON con los 5 posts de la tabla, posturas correctas, cero prohibidos. Si la extracción falla, iterar el prompt de `_build_vision_prompt` (la parte de LECTURA DE PANTALLA) y re-correr hasta que pase; los unit tests deben seguir verdes.

- [ ] **Step 5: Commit**

```bash
git add scraper_local/testdata/
git commit -m "test(vision): fixture de feed falso + smoke test real documentado"
```

---

## Self-Review (hecho al escribir el plan)

1. **Spec coverage:** transporte visión (Task 1) ✓, read_capture/prompt/saneo (Task 2) ✓, paceo+CLI (Task 3) ✓, smoke test con feed falso (Task 4) ✓, fuera de alcance respetado (sin dedup/browser/run) ✓.
2. **Placeholders:** ninguno — todo el código está completo en los steps.
3. **Consistencia de tipos:** `image: tuple[str, str] | None` idéntico en las 4 funciones del transporte y en el uso de `vision.read_capture`; `read_capture(image, red, candidatos=None, mime=None) -> list[dict] | None` consistente entre Tasks 2, 3 y 4.
