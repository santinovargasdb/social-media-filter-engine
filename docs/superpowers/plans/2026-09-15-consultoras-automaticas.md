# Consultoras automáticas (con fuente) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que la Boca de Urna traiga automáticamente los datos de 5 consultoras fijas (extraídos de noticias con IA) y los muestre en la tabla de comparación con su fuente clickeable, sin cargar el CSV a mano.

**Architecture:** Módulo nuevo `backend/pollsters.py` que, por cada consultora, busca notas con SerpAPI (búsqueda web), trae el texto del artículo, y extrae `consultora/fecha/candidato/%` con Gemini atado al link de la nota. `run_boca_de_urna` lo invoca cuando la request trae `auto_consultoras=true`, mergea con el CSV (el CSV pisa), y `compare_vs_pollsters` arrastra la fuente hasta el payload. El frontend suma un toggle y muestra el link por dato.

**Tech Stack:** Python (FastAPI, `requests`, stdlib), `gemini_client` (transporte Gemini compartido), `fetcher` (SerpAPI), Next.js/React + TypeScript (frontend). Tests con `pytest` y mocks (sin llamadas reales).

## Global Constraints

- Reusar el transporte existente: `gemini_client.run_with_rotation` para IA, `fetcher` para SerpAPI. No hablar con esas APIs "a mano".
- No agregar dependencias nuevas de pip: usar `requests` + biblioteca estándar.
- El análisis completo debe entrar bajo el timeout de 120 s del frontend: todo I/O de consultoras corre en PARALELO (`ThreadPoolExecutor`).
- Fidelidad: TODA fila de consultora traída automáticamente lleva `fuente_url` (link a la nota).
- El CSV manual sigue funcionando y **pisa** al automático en conflicto `(consultora, candidato)`.
- Compat: `auto_consultoras` default `false`; sin el flag, el comportamiento es idéntico al actual.
- Los tests mockean SerpAPI/Gemini/HTTP: NINGÚN test hace llamadas de red reales.
- Frontend sin dependencias nuevas; `npx tsc --noEmit` debe pasar.
- Import: `electoral.py` importa `pollsters` de forma DIFERIDA (dentro de `run_boca_de_urna`), y `pollsters.py` importa `canonical_key` de `electoral` a nivel de módulo. Así se evita el import circular.

---

## File Structure

- **Create** `backend/pollsters.py` — fetch + extracción + dedup de datos de consultoras. Única responsabilidad: convertir "5 nombres de consultoras" en filas `{consultora,fecha,candidato,porcentaje,fuente_url,fuente_titulo}`.
- **Create** `backend/test_pollsters.py` — tests del módulo con mocks.
- **Modify** `backend/fetcher.py` — agregar `search_serpapi_web` (búsqueda Google general, sin `site:`).
- **Modify** `backend/test_fetcher.py` — test de `search_serpapi_web`.
- **Modify** `backend/electoral.py` — `compare_vs_pollsters` arrastra la fuente; `run_boca_de_urna` acepta `auto_consultoras` y mergea; helper `_merge_pollster_rows`.
- **Modify** `backend/test_electoral.py` — tests de fuente en comparación y del merge/auto.
- **Modify** `backend/main.py` — `BocaDeUrnaRequest.auto_consultoras`; pasarlo a `run_boca_de_urna`.
- **Modify** `frontend/lib/urnaApi.ts` — `UrnaRequest.auto_consultoras`; campos de fuente en `UrnaConsultora`.
- **Modify** `frontend/components/urna/UrnaParamsBar.tsx` — toggle "Traer consultoras automáticamente".
- **Modify** `frontend/components/urna/ComparisonTable.tsx` — link a la fuente por dato + disclaimer.

---

### Task 1: Búsqueda web general en SerpAPI

**Files:**
- Modify: `backend/fetcher.py`
- Test: `backend/test_fetcher.py`

**Interfaces:**
- Consumes: `fetcher._serpapi_get_with_geo_fallback`, `fetcher._geo_params`, `fetcher.SERPAPI_API_KEY` (ya existen).
- Produces: `search_serpapi_web(query: str, max_results: int = 5, country: str = "ar") -> list[dict] | None` — devuelve `[{title, snippet, url, date}]`, `[]` sin resultados, o `None` ante error upstream.

- [ ] **Step 1: Write the failing test**

En `backend/test_fetcher.py`, agregar al final:

```python
def test_search_serpapi_web_parsea_organicos(monkeypatch):
    import fetcher
    fake = {"organic_results": [
        {"title": "Encuesta X", "snippet": "Milei 36%", "link": "https://n/1", "date": "2026-08-01"},
        {"title": "Nota Y", "snippet": "Kicillof 32%", "link": "https://n/2", "date": ""},
    ]}
    monkeypatch.setattr(fetcher, "SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr(fetcher, "_serpapi_get_with_geo_fallback", lambda params, tag: fake)
    out = fetcher.search_serpapi_web("Opinaia encuesta", max_results=5, country="ar")
    assert out == [
        {"title": "Encuesta X", "snippet": "Milei 36%", "url": "https://n/1", "date": "2026-08-01"},
        {"title": "Nota Y", "snippet": "Kicillof 32%", "url": "https://n/2", "date": ""},
    ]


def test_search_serpapi_web_sin_key_devuelve_none(monkeypatch):
    import fetcher
    monkeypatch.setattr(fetcher, "SERPAPI_API_KEY", "")
    assert fetcher.search_serpapi_web("q") is None


def test_search_serpapi_web_upstream_none(monkeypatch):
    import fetcher
    monkeypatch.setattr(fetcher, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(fetcher, "_serpapi_get_with_geo_fallback", lambda params, tag: None)
    assert fetcher.search_serpapi_web("q") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test_fetcher.py -k search_serpapi_web -v`
Expected: FAIL con `AttributeError: module 'fetcher' has no attribute 'search_serpapi_web'`.

- [ ] **Step 3: Write minimal implementation**

En `backend/fetcher.py`, agregar al final del archivo:

```python
def search_serpapi_web(
    query: str,
    max_results: int = 5,
    country: str = "ar",
) -> list[dict] | None:
    """Búsqueda web GENERAL en Google vía SerpAPI (SIN filtro site:), para traer
    notas/artículos (p. ej. encuestas de consultoras). Devuelve lista de dicts
    {title, snippet, url, date}, [] si no hay resultados, o None ante error de
    red/upstream (para no cachear vacíos espurios)."""
    if not SERPAPI_API_KEY:
        print("ERROR: SERPAPI_API_KEY no configurada en las variables de entorno.")
        return None
    gl, hl = _geo_params(country)
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERPAPI_API_KEY,
        "num": max_results,
        "hl": hl,
        "gl": gl,
    }
    data = _serpapi_get_with_geo_fallback(params, "web")
    if data is None:
        return None
    organic_results = data.get("organic_results", [])
    if not organic_results:
        print(f"DEBUG: SerpAPI[web] sin resultados orgánicos para '{query}'.")
        return []
    out: list[dict] = []
    for item in organic_results[:max_results]:
        out.append({
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": item.get("link", ""),
            "date": item.get("date", ""),
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test_fetcher.py -k search_serpapi_web -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/fetcher.py backend/test_fetcher.py
git commit -m "feat(pollsters): búsqueda web general en SerpAPI (sin filtro site:)"
```

---

### Task 2: Parseo y dedup de la extracción (funciones puras)

**Files:**
- Create: `backend/pollsters.py`
- Test: `backend/test_pollsters.py`

**Interfaces:**
- Consumes: `electoral.canonical_key` (import a nivel de módulo).
- Produces:
  - `CONSULTORAS_DEFAULT: list[str]`, `ELECCION_LABEL: str`
  - `_parse_extraction(parsed: list, consultora: str, articles: list[dict]) -> list[dict]` — filas `{consultora,fecha,candidato,porcentaje,fuente_url,fuente_titulo}`.
  - `_dedup_latest(rows: list[dict]) -> list[dict]` — por `(consultora, canonical(candidato))` conserva la fecha más reciente.

- [ ] **Step 1: Write the failing test**

Crear `backend/test_pollsters.py`:

```python
import pollsters as p


def _articles():
    return [
        {"url": "https://n/1", "title": "Nota 1", "text": "...", "date": "2026-08-01"},
        {"url": "https://n/2", "title": "Nota 2", "text": "...", "date": "2026-09-01"},
    ]


def test_parse_extraction_arma_filas_con_fuente():
    parsed = [
        {"id": "Art_0", "fecha": "2026-08-01", "filas": [
            {"candidato": "Javier Milei", "porcentaje": 36.3},
            {"candidato": "Axel Kicillof", "porcentaje": "32,3"},  # coma decimal
        ]},
    ]
    rows = p._parse_extraction(parsed, "Opinaia", _articles())
    assert len(rows) == 2
    assert rows[0] == {"consultora": "Opinaia", "fecha": "2026-08-01",
                       "candidato": "Javier Milei", "porcentaje": 36.3,
                       "fuente_url": "https://n/1", "fuente_titulo": "Nota 1"}
    assert rows[1]["porcentaje"] == 32.3  # coma normalizada


def test_parse_extraction_usa_fecha_del_articulo_si_falta():
    parsed = [{"id": "Art_1", "fecha": "", "filas": [{"candidato": "Milei", "porcentaje": 40}]}]
    rows = p._parse_extraction(parsed, "CB", _articles())
    assert rows[0]["fecha"] == "2026-09-01"  # cae a la date del artículo Art_1


def test_parse_extraction_descarta_pct_no_numerico_e_ids_inventados():
    parsed = [
        {"id": "Art_0", "filas": [{"candidato": "Milei", "porcentaje": "s/d"}]},
        {"id": "Art_99", "filas": [{"candidato": "X", "porcentaje": 10}]},  # id inexistente
    ]
    rows = p._parse_extraction(parsed, "Opinaia", _articles())
    assert rows == []


def test_dedup_latest_conserva_fecha_mas_reciente():
    rows = [
        {"consultora": "CB", "fecha": "2026-08-01", "candidato": "Javier Milei",
         "porcentaje": 40.0, "fuente_url": "u1", "fuente_titulo": "t1"},
        {"consultora": "CB", "fecha": "2026-09-01", "candidato": "javier  milei",
         "porcentaje": 42.0, "fuente_url": "u2", "fuente_titulo": "t2"},
    ]
    out = p._dedup_latest(rows)
    assert len(out) == 1 and out[0]["porcentaje"] == 42.0 and out[0]["fuente_url"] == "u2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test_pollsters.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'pollsters'`.

- [ ] **Step 3: Write minimal implementation**

Crear `backend/pollsters.py`:

```python
"""
Capa 3 — Consultoras automáticas (Boca de Urna).

Convierte una lista fija de consultoras en filas de comparación
{consultora, fecha, candidato, porcentaje, fuente_url, fuente_titulo}, extraídas
de notas periodísticas con Gemini y atadas SIEMPRE al link de la nota (fidelidad:
espejo fiel + fuente verificable).

No habla con SerpAPI ni Gemini "a mano": fetch vía fetcher.search_serpapi_web,
extracción vía gemini_client. `electoral` importa este módulo de forma diferida
para evitar el ciclo (este módulo importa canonical_key de electoral).
"""
from electoral import canonical_key


# Lista fija EDITABLE de consultoras a seguir. Elegidas por prestigio metodológico
# y equilibrio de espectro (3 neutrales + 1 de cada polo). La "afiliación" es
# percepción pública debatida, ninguna se declara partidaria.
CONSULTORAS_DEFAULT = [
    "Opinaia",           # neutral / profesional
    "Poliarquía",        # neutral / establishment
    "Management & Fit",  # neutral / centro
    "CB Consultora",     # suele medir mejor al oficialismo/LLA
    "Zuban Córdoba",     # suele medir mejor al peronismo/kirchnerismo
]

# Descriptor de la elección para la query (editable, para no hardcodear el año).
ELECCION_LABEL = "presidencial 2027"


def _parse_pct(raw) -> float:
    """'42,5' | '42.5' | 42 -> float. Lanza ValueError/TypeError si no es numérico."""
    return float(str(raw).strip().replace(",", "."))


def _parse_extraction(parsed: list, consultora: str, articles: list[dict]) -> list[dict]:
    """Mapea la respuesta de Gemini (espejo por 'Art_i') a filas de consultora con
    su fuente. Descarta ids inventados y porcentajes no numéricos. La fecha sale de
    la fila; si no vino, cae a la 'date' del artículo."""
    by_id = {f"Art_{i}": a for i, a in enumerate(articles)}
    rows: list[dict] = []
    for item in parsed or []:
        art = by_id.get(item.get("id", ""))
        if art is None:
            continue
        fecha = (item.get("fecha") or "").strip() or (art.get("date") or "").strip()
        for fila in item.get("filas", []) or []:
            candidato = (fila.get("candidato") or "").strip()
            if not candidato:
                continue
            try:
                pct = _parse_pct(fila.get("porcentaje"))
            except (TypeError, ValueError):
                continue
            rows.append({
                "consultora": consultora,
                "fecha": fecha,
                "candidato": candidato,
                "porcentaje": pct,
                "fuente_url": art.get("url", ""),
                "fuente_titulo": art.get("title", ""),
            })
    return rows


def _dedup_latest(rows: list[dict]) -> list[dict]:
    """Por (consultora, candidato canónico) conserva la fila de fecha más reciente."""
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r["consultora"].strip().lower(), canonical_key(r["candidato"]))
        cur = best.get(key)
        if cur is None or r.get("fecha", "") > cur.get("fecha", ""):
            best[key] = r
    return list(best.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test_pollsters.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/pollsters.py backend/test_pollsters.py
git commit -m "feat(pollsters): parseo/dedup de extracción con fuente + lista fija de consultoras"
```

---

### Task 3: Orquestación `fetch_pollster_rows` (fetch + artículo + Gemini + caché)

**Files:**
- Modify: `backend/pollsters.py`
- Test: `backend/test_pollsters.py`

**Interfaces:**
- Consumes: `fetcher.search_serpapi_web` (Task 1), `gemini_client.run_with_rotation`, `_parse_extraction`, `_dedup_latest` (Task 2).
- Produces:
  - `fetch_pollster_rows(fecha_desde=None, country="ar", consultoras=None) -> tuple[list[dict], list[str]]` — `(rows, warnings)`.
  - `_fetch_article_text(url: str) -> str` — texto legible best-effort ("" si falla).
  - `_build_extract_prompt(consultora: str, articles: list[dict]) -> str`.

- [ ] **Step 1: Write the failing test**

Agregar a `backend/test_pollsters.py`:

```python
def test_fetch_pollster_rows_happy(monkeypatch):
    import fetcher, gemini_client as gc
    # 1 resultado por consultora, misma url; texto vía _fetch_article_text mockeado.
    monkeypatch.setattr(fetcher, "search_serpapi_web",
                        lambda q, max_results=5, country="ar": [
                            {"title": "Nota", "snippet": "s", "url": "https://n/x", "date": "2026-09-01"}])
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto con números")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: ([
        {"id": "Art_0", "fecha": "2026-09-01",
         "filas": [{"candidato": "Javier Milei", "porcentaje": 36.3}]},
    ], None))
    p._CACHE.clear()
    rows, warnings = p.fetch_pollster_rows(consultoras=["Opinaia", "CB Consultora"])
    # Una fila por consultora (misma url pero distinta consultora -> no se pisan).
    consultoras = sorted(r["consultora"] for r in rows)
    assert consultoras == ["CB Consultora", "Opinaia"]
    assert all(r["fuente_url"] == "https://n/x" and r["candidato"] == "Javier Milei" for r in rows)


def test_fetch_pollster_rows_una_consultora_sin_resultados(monkeypatch):
    import fetcher, gemini_client as gc

    def fake_search(q, max_results=5, country="ar"):
        return [] if "Opinaia" in q else [{"title": "t", "snippet": "s", "url": "u", "date": ""}]

    monkeypatch.setattr(fetcher, "search_serpapi_web", fake_search)
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (
        [{"id": "Art_0", "filas": [{"candidato": "Milei", "porcentaje": 40}]}], None))
    p._CACHE.clear()
    rows, warnings = p.fetch_pollster_rows(consultoras=["Opinaia", "CB Consultora"])
    assert [r["consultora"] for r in rows] == ["CB Consultora"]
    assert any("Opinaia" in w for w in warnings)


def test_fetch_pollster_rows_cachea(monkeypatch):
    import fetcher, gemini_client as gc
    llamadas = {"n": 0}

    def fake_search(q, max_results=5, country="ar"):
        llamadas["n"] += 1
        return [{"title": "t", "snippet": "s", "url": "u", "date": "2026-09-01"}]

    monkeypatch.setattr(fetcher, "search_serpapi_web", fake_search)
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (
        [{"id": "Art_0", "filas": [{"candidato": "Milei", "porcentaje": 40}]}], None))
    p._CACHE.clear()
    p.fetch_pollster_rows(consultoras=["Opinaia"])
    n1 = llamadas["n"]
    p.fetch_pollster_rows(consultoras=["Opinaia"])  # cache HIT -> no re-busca
    assert llamadas["n"] == n1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test_pollsters.py -k fetch_pollster_rows -v`
Expected: FAIL con `AttributeError: module 'pollsters' has no attribute 'fetch_pollster_rows'` (o `_CACHE`).

- [ ] **Step 3: Write minimal implementation**

En `backend/pollsters.py`, agregar imports arriba (debajo del docstring, junto al import existente):

```python
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import fetcher
import gemini_client
```

> Nota: se importa `fetcher` como MÓDULO (no `from fetcher import search_serpapi_web`),
> para que el `monkeypatch.setattr(fetcher, "search_serpapi_web", ...)` de los tests
> tome efecto (con `from ... import` el nombre queda ligado en `pollsters` y el parche
> no llegaría).

Y agregar al final del módulo:

```python
# ── Config de fetch ───────────────────────────────────────────────────────────
ARTICULOS_POR_CONSULTORA = 2          # cuántas notas mirar por consultora
POLLSTER_FETCH_CONCURRENCY = 5        # consultoras en paralelo
_ARTICLE_TIMEOUT = 15                 # seg por artículo
_ARTICLE_MAX_CHARS = 8000            # tope de texto que ve Gemini por nota

# ── Caché de TTL largo (las encuestas cambian lento) ──────────────────────────
_CACHE_TTL = 6 * 3600
_CACHE: dict[tuple, tuple[float, tuple[list, list]]] = {}

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _fetch_article_text(url: str) -> str:
    """Trae el texto legible de la nota (best-effort, sin dependencias). "" si falla
    (paywall/anti-bot/timeout); el caller cae al snippet de SerpAPI."""
    if not url:
        return ""
    try:
        resp = requests.get(url, timeout=_ARTICLE_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; SMATA-Monitor/1.0)"})
        resp.raise_for_status()
        html = resp.text
    except requests.exceptions.RequestException as e:
        print(f"DEBUG pollsters: no se pudo traer '{url}': {e}")
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:_ARTICLE_MAX_CHARS]


def _build_extract_prompt(consultora: str, articles: list[dict]) -> str:
    items = [{"id": f"Art_{i}", "titulo": a.get("title", ""), "texto": a.get("text", "")}
             for i, a in enumerate(articles)]
    items_text = json.dumps(items, ensure_ascii=False, indent=2)
    return f"""Sos un analista que extrae datos de encuestas electorales argentinas de notas periodísticas.

Vas a recibir una lista de notas en JSON, cada una con un "id" ("Art_0", "Art_1", ...), su "titulo" y su "texto". TODAS son sobre encuestas de la consultora "{consultora}".

Por cada nota, extraé ÚNICAMENTE los porcentajes de intención de voto presidencial que la consultora "{consultora}" reporta de forma EXPLÍCITA en el texto. Reglas:
1. Extraé solo números que estén literalmente en el texto. NO estimes ni inventes. Si la nota no trae porcentajes claros de esta consultora, devolvé "filas": [].
2. "candidato": nombre COMPLETO y CANÓNICO de la persona (ej. "Milei" -> "Javier Milei").
3. NO incluyas opciones que no son candidatos: "voto en blanco", "en blanco", "impugnado", "indeciso", "no sabe / no contesta", "ninguno", "otros".
4. "fecha": fecha del sondeo en formato YYYY-MM-DD si aparece; si no, "".

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código) que sea un ESPEJO EXACTO de los ids recibidos, uno por nota. Formato exacto:
[
  {{ "id": "Art_0", "fecha": "2026-08-01", "filas": [ {{ "candidato": "Javier Milei", "porcentaje": 36.3 }} ] }}
]

Notas a procesar:
{items_text}"""


def _fetch_one_consultora(consultora: str, country: str) -> tuple[list[dict], list[str]]:
    query = f'"{consultora}" encuesta intención de voto {ELECCION_LABEL}'
    resultados = fetcher.search_serpapi_web(query, max_results=ARTICULOS_POR_CONSULTORA, country=country)
    if resultados is None:
        return [], [f"{consultora}: el buscador no respondió."]
    if not resultados:
        return [], [f"No se encontraron encuestas recientes de {consultora}."]
    articles = []
    for r in resultados:
        text = _fetch_article_text(r.get("url", "")) or r.get("snippet", "")
        articles.append({"url": r.get("url", ""), "title": r.get("title", ""),
                         "text": text, "date": r.get("date", "")})
    parsed, _status = gemini_client.run_with_rotation(_build_extract_prompt(consultora, articles))
    if parsed is None:
        return [], [f"{consultora}: no se pudo extraer (IA no disponible)."]
    return _parse_extraction(parsed, consultora, articles), []


def fetch_pollster_rows(fecha_desde: str | None = None, country: str = "ar",
                        consultoras: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Trae filas de comparación de las consultoras seguidas, cada una con su fuente.
    Devuelve (rows, warnings). Cachea con TTL largo (las encuestas cambian lento)."""
    consultoras = consultoras or CONSULTORAS_DEFAULT
    cache_key = (tuple(consultoras), (country or "ar").strip().lower())
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL:
        print(f"DEBUG pollsters: cache HIT {cache_key}")
        return cached[1]

    def _one(consultora: str) -> tuple[list[dict], list[str]]:
        try:
            return _fetch_one_consultora(consultora, country)
        except Exception as e:
            print(f"ERROR pollsters[{consultora}]: {e}")
            return [], [f"{consultora}: error al procesar."]

    with ThreadPoolExecutor(max_workers=min(POLLSTER_FETCH_CONCURRENCY, len(consultoras))) as ex:
        resultados = list(ex.map(_one, consultoras))

    rows: list[dict] = []
    warnings: list[str] = []
    for crows, cwarn in resultados:
        rows.extend(crows)
        warnings.extend(cwarn)
    rows = _dedup_latest(rows)
    result = (rows, warnings)
    _CACHE[cache_key] = (now, result)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test_pollsters.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/pollsters.py backend/test_pollsters.py
git commit -m "feat(pollsters): fetch_pollster_rows (búsqueda + artículo + extracción + caché)"
```

---

### Task 4: `compare_vs_pollsters` arrastra la fuente

**Files:**
- Modify: `backend/electoral.py` (función `compare_vs_pollsters`)
- Test: `backend/test_electoral.py`

**Interfaces:**
- Consumes: filas con campos opcionales `fuente_url`, `fuente_titulo`, `fecha` (las del CSV no los traen).
- Produces: cada dict en `comparacion[i]["consultoras"]` suma `fuente_url`, `fuente_titulo`, `fecha` (None/"" si no hay).

- [ ] **Step 1: Write the failing test**

Agregar a `backend/test_electoral.py`:

```python
def test_compare_arrastra_la_fuente():
    candidatos = [{"nombre": "Javier Milei", "pct": 44.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    rows = [{"consultora": "Opinaia", "fecha": "2026-09-01", "candidato": "Javier Milei",
             "porcentaje": 40.0, "fuente_url": "https://n/1", "fuente_titulo": "Nota Opinaia"}]
    comp, _w = el.compare_vs_pollsters(candidatos, rows)
    cell = comp[0]["consultoras"][0]
    assert cell["consultora"] == "Opinaia" and cell["pct"] == 40.0 and cell["gap"] == 4.0
    assert cell["fuente_url"] == "https://n/1" and cell["fuente_titulo"] == "Nota Opinaia"
    assert cell["fecha"] == "2026-09-01"


def test_compare_sin_fuente_csv_no_rompe():
    # Filas de CSV (sin campos de fuente) -> las claves de fuente quedan None/"".
    candidatos = [{"nombre": "Milei", "pct": 44.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    rows = [{"consultora": "X", "fecha": "2026-08-01", "candidato": "Milei", "porcentaje": 42.0}]
    comp, _w = el.compare_vs_pollsters(candidatos, rows)
    cell = comp[0]["consultoras"][0]
    assert cell["pct"] == 42.0 and cell.get("fuente_url") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test_electoral.py -k "arrastra_la_fuente or sin_fuente_csv" -v`
Expected: FAIL con `KeyError: 'fuente_url'` en el primer test.

- [ ] **Step 3: Write minimal implementation**

En `backend/electoral.py`, dentro de `compare_vs_pollsters`, reemplazar el bloque que arma `por_candidato` y la comprensión de `consultoras`.

Reemplazar:

```python
    # Agrupar por candidato_key -> {consultora: pct}.
    por_candidato: dict[str, dict[str, float]] = {}
    for (cand_key, consultora), r in latest.items():
        por_candidato.setdefault(cand_key, {})[consultora] = r["porcentaje"]
```

por:

```python
    # Agrupar por candidato_key -> {consultora: fila_completa} (para arrastrar la fuente).
    por_candidato: dict[str, dict[str, dict]] = {}
    for (cand_key, consultora), r in latest.items():
        por_candidato.setdefault(cand_key, {})[consultora] = r
```

Y reemplazar el bloque:

```python
        consultoras_pct: dict[str, float] = {}
        for csv_key, por_cons in por_candidato.items():
            if _keys_match(key, csv_key):
                matched_csv_keys.add(csv_key)
                for consultora, pct in por_cons.items():
                    if consultora in consultoras_pct:
                        consultoras_pct[consultora] = round(
                            (consultoras_pct[consultora] + pct) / 2, 1
                        )
                    else:
                        consultoras_pct[consultora] = pct
        consultoras = [
            {"consultora": nombre, "pct": pct, "gap": round(c["pct"] - pct, 1)}
            for nombre, pct in sorted(consultoras_pct.items())
        ]
        if consultoras_pct:
            promedio = round(sum(consultoras_pct.values()) / len(consultoras_pct), 1)
            gap_promedio = round(c["pct"] - promedio, 1)
        else:
            promedio, gap_promedio = None, None
```

por:

```python
        consultoras_data: dict[str, dict] = {}
        for csv_key, por_cons in por_candidato.items():
            if _keys_match(key, csv_key):
                matched_csv_keys.add(csv_key)
                for consultora, r in por_cons.items():
                    pct = r["porcentaje"]
                    if consultora in consultoras_data:
                        prev = consultoras_data[consultora]
                        prev["pct"] = round((prev["pct"] + pct) / 2, 1)
                        # conservar la fuente de la fila más reciente
                        if r.get("fecha", "") > prev.get("fecha", ""):
                            prev["fuente_url"] = r.get("fuente_url")
                            prev["fuente_titulo"] = r.get("fuente_titulo")
                            prev["fecha"] = r.get("fecha", "")
                    else:
                        consultoras_data[consultora] = {
                            "pct": pct,
                            "fuente_url": r.get("fuente_url"),
                            "fuente_titulo": r.get("fuente_titulo"),
                            "fecha": r.get("fecha", ""),
                        }
        consultoras = [
            {"consultora": nombre, "pct": d["pct"], "gap": round(c["pct"] - d["pct"], 1),
             "fuente_url": d.get("fuente_url"), "fuente_titulo": d.get("fuente_titulo"),
             "fecha": d.get("fecha", "")}
            for nombre, d in sorted(consultoras_data.items())
        ]
        if consultoras_data:
            promedio = round(sum(d["pct"] for d in consultoras_data.values()) / len(consultoras_data), 1)
            gap_promedio = round(c["pct"] - promedio, 1)
        else:
            promedio, gap_promedio = None, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test_electoral.py -k "compare" -v`
Expected: todos los tests `compare*` pasan (los nuevos y los existentes `test_compare_*`).

- [ ] **Step 5: Commit**

```bash
git add backend/electoral.py backend/test_electoral.py
git commit -m "feat(urna): compare_vs_pollsters arrastra la fuente por consultora"
```

---

### Task 5: `run_boca_de_urna` con `auto_consultoras` + merge

**Files:**
- Modify: `backend/electoral.py` (`run_boca_de_urna` + nuevo `_merge_pollster_rows`)
- Test: `backend/test_electoral.py`

**Interfaces:**
- Consumes: `pollsters.fetch_pollster_rows` (Task 3, import diferido).
- Produces: `run_boca_de_urna(..., auto_consultoras: bool = False)`; `_merge_pollster_rows(auto_rows, manual_rows) -> list[dict]` (manual pisa).

- [ ] **Step 1: Write the failing test**

Agregar a `backend/test_electoral.py`:

```python
def test_merge_pollster_rows_csv_pisa_auto():
    auto = [{"consultora": "Opinaia", "fecha": "2026-09-01", "candidato": "Javier Milei",
             "porcentaje": 40.0, "fuente_url": "u", "fuente_titulo": "t"}]
    manual = [{"consultora": "Opinaia", "fecha": "2026-08-01", "candidato": "javier milei",
               "porcentaje": 48.0}]  # misma consultora+candidato -> pisa
    out = el._merge_pollster_rows(auto, manual)
    assert len(out) == 1 and out[0]["porcentaje"] == 48.0


def test_run_boca_auto_consultoras_llena_comparacion(monkeypatch):
    import normalizer, gemini_client as gc, pollsters
    posts = [{"id": "1", "network": "twitter", "author": "", "author_url": "", "text": "Milei",
              "date": "", "post_url": "u1", "relevance_score": 80, "relevance_level": "alta",
              "matched_terms": [], "video_url": None}]
    monkeypatch.setattr(normalizer, "fetch_raw_posts", lambda **kw: (posts, False))
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: ([
        {"id": "Post_0", "es_electoral": True, "cita": "Milei",
         "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}]},
    ], None))
    monkeypatch.setattr(pollsters, "fetch_pollster_rows", lambda **kw: ([
        {"consultora": "Opinaia", "fecha": "2026-09-01", "candidato": "Javier Milei",
         "porcentaje": 41.0, "fuente_url": "https://n/1", "fuente_titulo": "Nota"}], []))
    out = el.run_boca_de_urna(keywords=["elecciones"], networks=["twitter"], date=None,
                              country="ar", pollster_csv="", auto_consultoras=True)
    milei = next(c for c in out["comparacion"]
                 if el.canonical_key(c["candidato"]) == el.canonical_key("Javier Milei"))
    assert milei["consultoras"][0]["fuente_url"] == "https://n/1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test_electoral.py -k "merge_pollster or auto_consultoras" -v`
Expected: FAIL — `_merge_pollster_rows` no existe / `run_boca_de_urna` no acepta `auto_consultoras`.

- [ ] **Step 3: Write minimal implementation**

En `backend/electoral.py`, agregar el helper ANTES de `run_boca_de_urna`:

```python
def _merge_pollster_rows(auto_rows: list[dict], manual_rows: list[dict]) -> list[dict]:
    """Une filas automáticas (con fuente) y del CSV. El CSV PISA al automático en
    conflicto (consultora, candidato canónico): es el dato vetado a mano."""
    merged: dict[tuple, dict] = {}
    for r in auto_rows:
        merged[(r["consultora"].strip().lower(), canonical_key(r["candidato"]))] = r
    for r in manual_rows:
        merged[(r["consultora"].strip().lower(), canonical_key(r["candidato"]))] = r
    return list(merged.values())
```

Modificar la firma y el arranque de `run_boca_de_urna`:

Reemplazar:

```python
def run_boca_de_urna(keywords: list[str], networks: list[str], date: str | None,
                     country: str, pollster_csv: str) -> dict:
```

por:

```python
def run_boca_de_urna(keywords: list[str], networks: list[str], date: str | None,
                     country: str, pollster_csv: str, auto_consultoras: bool = False) -> dict:
```

Y justo después de la línea `warnings = list(csv_warnings)` (dentro de la función), insertar:

```python
    # Consultoras automáticas (opcional): trae filas con fuente y las mergea con el
    # CSV. El CSV pisa al automático. Import diferido para evitar el ciclo con pollsters.
    if auto_consultoras:
        import pollsters
        auto_rows, auto_warnings = pollsters.fetch_pollster_rows(fecha_desde=date, country=country)
        warnings.extend(auto_warnings)
        pollster_rows = _merge_pollster_rows(auto_rows, pollster_rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test_electoral.py -v`
Expected: toda la suite de `test_electoral.py` pasa (nuevos + existentes).

- [ ] **Step 5: Commit**

```bash
git add backend/electoral.py backend/test_electoral.py
git commit -m "feat(urna): run_boca_de_urna trae consultoras automáticas (flag + merge, CSV pisa)"
```

---

### Task 6: Endpoint — `auto_consultoras` en la request

**Files:**
- Modify: `backend/main.py`
- Test: `backend/test_main_urna.py`

**Interfaces:**
- Consumes: `run_boca_de_urna(..., auto_consultoras=...)` (Task 5).
- Produces: `BocaDeUrnaRequest.auto_consultoras: bool = False`, reenviado al servicio.

- [ ] **Step 1: Write the failing test**

Agregar a `backend/test_main_urna.py`:

```python
def test_endpoint_pasa_auto_consultoras(monkeypatch):
    capturado = {}

    def fake_run(**kw):
        capturado.update(kw)
        return {"candidatos": [], "evidencia": [], "comparacion": [],
                "meta": {"total_posts": 0, "posts_electorales": 0, "disclaimer": "x", "warnings": []}}

    monkeypatch.setattr(electoral, "run_boca_de_urna", fake_run)
    asyncio.run(main.boca_de_urna_endpoint(_req(auto_consultoras=True)))
    assert capturado["auto_consultoras"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test_main_urna.py -k auto_consultoras -v`
Expected: FAIL — `BocaDeUrnaRequest` no acepta `auto_consultoras` (o `capturado` no lo tiene).

- [ ] **Step 3: Write minimal implementation**

En `backend/main.py`, en `class BocaDeUrnaRequest`, agregar el campo:

```python
    auto_consultoras: bool = False
```

Y en `boca_de_urna_endpoint`, dentro de `asyncio.to_thread(electoral.run_boca_de_urna, ...)`, agregar el argumento:

```python
            auto_consultoras=request.auto_consultoras,
```

(justo después de `pollster_csv=request.pollster_csv,`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest test_main_urna.py -v`
Expected: todos pasan.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/test_main_urna.py
git commit -m "feat(urna): endpoint acepta auto_consultoras y lo reenvía al servicio"
```

---

### Task 7: Frontend — toggle + fuente en la tabla

**Files:**
- Modify: `frontend/lib/urnaApi.ts`
- Modify: `frontend/components/urna/UrnaParamsBar.tsx`
- Modify: `frontend/components/urna/ComparisonTable.tsx`
- Test: `npx tsc --noEmit` (typecheck)

**Interfaces:**
- Consumes: payload con `comparacion[i].consultoras[j].{fuente_url?, fuente_titulo?, fecha?}` (Task 4) y request con `auto_consultoras` (Task 6).
- Produces: UI con toggle y link a la fuente por dato.

- [ ] **Step 1: Extender los tipos en `urnaApi.ts`**

Reemplazar la interfaz `UrnaConsultora` y agregar el campo a `UrnaRequest`:

```typescript
export interface UrnaConsultora {
  consultora: string; pct: number; gap: number;
  fuente_url?: string; fuente_titulo?: string; fecha?: string;
}
```

```typescript
export interface UrnaRequest {
  keywords: string[]; networks: string[]; date: string | null; country: string;
  pollster_csv: string; auto_consultoras: boolean;
}
```

- [ ] **Step 2: Agregar el toggle en `UrnaParamsBar.tsx`**

Agregar el estado (junto a los otros `useState`):

```typescript
  const [autoConsultoras, setAutoConsultoras] = useState(false);
```

Incluir el flag en el objeto que arma `submit()`:

```typescript
      pollster_csv: csvText,
      auto_consultoras: autoConsultoras,
```

Y agregar el control antes del botón "Analizar" (después del `<label>` del CSV):

```tsx
      <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px",
                      color: "var(--text-secondary)", cursor: "pointer" }}>
        <input type="checkbox" checked={autoConsultoras}
               onChange={(e) => setAutoConsultoras(e.target.checked)} />
        ⟳ Traer consultoras automáticamente
      </label>
```

- [ ] **Step 3: Mostrar la fuente en `ComparisonTable.tsx`**

Reemplazar la celda de cada consultora para incluir el link. Reemplazar:

```tsx
                  <td key={name} style={td}>
                    {cell ? <>{cell.pct}% <span style={{ color: gapColor(cell.gap), fontSize: "11px" }}>({fmt(cell.gap)})</span></> : "—"}
                  </td>
```

por:

```tsx
                  <td key={name} style={td}>
                    {cell ? (
                      <>
                        {cell.pct}% <span style={{ color: gapColor(cell.gap), fontSize: "11px" }}>({fmt(cell.gap)})</span>
                        {cell.fuente_url && (
                          <a href={cell.fuente_url} target="_blank" rel="noopener noreferrer"
                             title={`${cell.fuente_titulo || "fuente"}${cell.fecha ? " · " + cell.fecha : ""}`}
                             style={{ marginLeft: "4px", fontSize: "11px", textDecoration: "none" }}>↗</a>
                        )}
                      </>
                    ) : "—"}
                  </td>
```

Y agregar, después de `</table>` (antes de cerrar el `<div>`), el disclaimer:

```tsx
      <p style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "8px" }}>
        Datos de consultoras extraídos de fuentes públicas — verificá en la fuente (↗).
      </p>
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0 (sin errores).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/urnaApi.ts frontend/components/urna/UrnaParamsBar.tsx frontend/components/urna/ComparisonTable.tsx
git commit -m "feat(urna): toggle de consultoras automáticas y link a la fuente en la tabla"
```

---

### Task 8: Verificación integral + deploy

**Files:** ninguno (verificación).

- [ ] **Step 1: Suite completo del backend**

Run: `cd backend && python -m pytest -q`
Expected: todo verde (incluye los módulos nuevos y los existentes).

- [ ] **Step 2: Typecheck frontend**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit/push si quedó algo suelto y verificar deploy**

```bash
git status
git push origin main
```

Luego, tras el deploy de Render, verificar en vivo con un POST que incluya `"auto_consultoras": true` y confirmar que `comparacion[].consultoras[]` traiga `fuente_url`, y medir que el tiempo siga bajo 120 s. Si se pasa, bajar `ARTICULOS_POR_CONSULTORA` o achicar `CONSULTORAS_DEFAULT`.

---

## Notas de implementación

- **Cuota:** este flujo agrega ~5 búsquedas SerpAPI + ~5-10 fetches de artículo + hasta 5 llamadas Gemini por análisis (solo cuando el toggle está prendido y no hay cache HIT). El caché de 6 h amortigua análisis repetidos.
- **Fidelidad:** el prompt de extracción prohíbe explícitamente inventar números; toda fila lleva `fuente_url`. Un número mal leído es verificable por el link.
- **Límites v1:** no lee infografías/imágenes; cobertura = notas recientes indexadas de esas 5 consultoras.
