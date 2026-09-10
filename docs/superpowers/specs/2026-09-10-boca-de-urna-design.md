# Boca de Urna — Termómetro de Redes · Diseño

**Fecha:** 2026-09-10
**Proyecto:** Monitor de Medios SMATA
**Estado:** Diseño aprobado — listo para plan de implementación

## 1. Resumen

Nueva funcionalidad para el Departamento de Prensa de SMATA: una "boca de urna"
que, a partir de publicaciones públicas en redes sociales, estima el clima de
apoyo hacia cada candidato de cara a las próximas elecciones presidenciales, lo
muestra en gráficos con la evidencia (las citas) que sostiene cada número, y lo
compara contra los porcentajes publicados por consultoras de opinión pública
para exponer la **brecha** entre lo que dicen las redes y lo que dicen las
consultoras.

Es un **termómetro direccional del clima en redes**, no una encuesta
representativa ni una proyección de resultado electoral (ver §9, Encuadre).

## 2. Objetivos

- Detectar automáticamente los candidatos mencionados en las publicaciones.
- Calcular por candidato un porcentaje basado en **sentimiento neto**
  (menciones a favor − en contra), como proxy de intención de voto.
- Mostrar la evidencia: las citas textuales y el link al post que respalda cada
  postura, de modo que cualquier número sea auditable.
- Comparar el ranking de redes contra los porcentajes de cada consultora
  (cargados por CSV) y mostrar la brecha por candidato.
- Todo esto en una página dedicada, sin tocar el monitor de prensa existente.

## 3. No-objetivos (fuera de alcance)

- No se mide "precisión histórica contra el resultado real" de una elección
  pasada (solo brecha redes-vs-consultoras en el momento actual).
- No se persiste nada: no hay base de datos, no hay histórico de corridas.
- No se traen comentarios/replies reales vía APIs de plataforma; se analizan
  las **publicaciones públicas** que ya trae el fetch actual (SerpAPI/Google).
- No se hace scraping de sitios de consultoras (los datos entran por CSV).

## 4. Decisiones tomadas (brainstorming)

| Tema | Decisión |
|------|----------|
| Fuente de datos | Reutilizar el fetch actual (SerpAPI/Google): publicaciones públicas, no replies |
| Métrica | **Sentimiento neto** (a favor − en contra), normalizado entre candidatos |
| Datos de consultoras | **CSV importado** por el usuario, con esquema fijo |
| Definición de candidatos | **Auto-detección** con Gemini + normalización de alias |
| Significado de "precisión" | **Brecha redes-vs-consultoras en el momento actual** |
| Ubicación en la UI | **Página nueva** `/boca-de-urna` (monitor actual intacto) |
| Persistencia | **Stateless** (nada se guarda) |
| Arquitectura backend | Enfoque A: módulo `electoral.py` aislado + endpoint nuevo |
| Reuso de Gemini | Extraer `gemini_client.py` compartido (refactor quirúrgico) |
| Layout frontend | Opción C: dashboard denso de dos columnas |
| Librería de charts | Ninguna nueva: barras con CSS puro |

## 5. Arquitectura y flujo de datos

Respeta la disciplina de 3 capas del backend
(`main.py` → `electoral.py` → `fetcher.py`), en paralelo a
`normalizer.py`.

```
Usuario (/boca-de-urna)
  │  1. Término electoral (ej. "elecciones presidenciales 2027") + país
  │     + fecha desde + CSV de consultoras (opcional)
  ▼
POST /api/boca-de-urna   ──────────────────  Capa 1 (main.py)
  │     valida request; el CSV llega como texto en el JSON
  ▼
electoral.py  ────────────────────────────  Capa 3 NUEVA (hermana de normalizer)
  │  2. fetch_posts()  →  reutiliza fetcher.py (SerpAPI) tal cual  ── Capa 2
  │  3. análisis Gemini electoral (pasada nueva):
  │        por post → { candidatos[], postura, confianza, cita, es_electoral }
  │  4. normaliza/reconcilia nombres de candidatos (alias, acentos, may/min)
  │  5. agrega en SENTIMIENTO NETO por candidato → %
  │  6. parsea CSV y cruza con las consultoras → brecha por candidato
  ▼
Respuesta JSON: { candidatos[], evidencia[], comparacion[], meta{} }
  ▼
Frontend dibuja: barras (redes) + tabla comparativa + panel de citas
```

Invariantes:

- `fetcher.py` **no se toca** (la boca de urna consume el fetch igual que el
  monitor).
- `normalizer.py` **no cambia su lógica**; solo pasa a importar del nuevo
  `gemini_client.py`.
- Stateless: el CSV se procesa en la request; la respuesta es autocontenida.

## 6. Backend

### 6.1 Capa 1 — endpoint en `main.py`

```python
POST /api/boca-de-urna

class BocaDeUrnaRequest(BaseModel):
    keywords: List[str] = []          # ej. ["elecciones presidenciales 2027"]
    networks: List[str] = []
    date: Optional[str] = None
    country: str = "ar"
    pollster_csv: str = ""            # texto crudo del CSV, opcional
```

El frontend lee el archivo con `FileReader` y envía el **texto del CSV como
string** dentro del JSON, para mantener el cliente JSON actual (sin multipart).
El parseo y la validación del CSV son autoritativos en el backend.

Manejo de errores del endpoint:
- `UpstreamUnavailableError` → **503** con mensaje claro (igual que hoy).
- CSV inválido (irrecuperable) → **400** con detalle.
- Casos vacíos (sin posts, sin candidatos, sin CSV) → **200** con explicación
  en `meta`.

### 6.2 Esquema del CSV de consultoras

Parseado con el módulo `csv` de la stdlib (sin dependencias nuevas). Columnas
fijas:

```csv
consultora,fecha,candidato,porcentaje
Consultora X,2026-08-01,Javier Milei,42.5
Consultora X,2026-08-01,Axel Kicillof,38.0
Consultora Y,2026-08-15,Javier Milei,40.1
```

- `fecha` en ISO `YYYY-MM-DD`. Si una consultora tiene varias fechas, se usa la
  más reciente por consultora+candidato.
- `porcentaje` numérico (se acepta coma o punto decimal; se normaliza).
- Filas inválidas se saltean y se listan en `meta.warnings` (no abortan todo).

### 6.3 Capa 3 — `electoral.py`

Funciones:

- `run_boca_de_urna(...)` — orquesta: `fetch_posts()` → análisis Gemini →
  normalización → agregación → parseo CSV → comparación. Devuelve el payload.
- `_analyze_posts_electoral(posts)` — pasada Gemini nueva. Respuesta espejo por
  ID, reusando la **regla de aislamiento** de `normalizer`. Shape por post:
  ```json
  { "id": "Post_0",
    "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}],
    "cita": "fragmento textual que justifica la postura",
    "es_electoral": true }
  ```
  Posturas: `a_favor` / `en_contra` / `neutro`. Post sin candidato o no
  electoral → `candidatos: []`.
- `_canonicalize_candidates(...)` — unifica alias. Gemini devuelve el nombre
  canónico; se mergea sin acentos y case-insensitive.
- `_aggregate_net_sentiment(...)` — por candidato acumula `pos`, `neg`, `neu`,
  `menciones`.
- `_parse_pollster_csv(text)` — CSV → lista de filas validadas + warnings.
- `_compare_vs_pollsters(social, pollsters)` — brecha por candidato contra cada
  consultora + promedio.

### 6.4 Fórmula de agregación (sentimiento neto)

Por candidato *i*:

```
net_i = pos_i - neg_i
pct_i = max(net_i, 0) / Σ_j max(net_j, 0)   · 100
```

- Si `Σ max(net_j,0) == 0` (todos los netos ≤ 0), se cae a **share por
  volumen**: `pct_i = menciones_i / Σ menciones_j · 100`, y se anota el fallback
  en `meta.warnings`.
- Se exponen siempre los crudos `pos/neg/neu/menciones` para transparencia.
- **Uso de `confianza`:** las clasificaciones por debajo de un umbral
  (`CONF_MIN = 0.5`) se descartan antes de contar (no suman a `pos/neg/neu`) y
  se cuentan aparte en `meta` como "menciones de baja confianza". La confianza
  **no pondera** los conteos (el neto es por conteo simple, como se acordó);
  solo filtra ruido y ordena la evidencia (las citas de mayor confianza primero).

### 6.5 Comparación vs consultoras

Por candidato: `redes_pct`, y por cada consultora su `pct` (fecha más reciente)
y `gap = redes_pct − consultora_pct`. Más `promedio_consultoras` y
`gap_promedio`. Reconciliación de nombres sin acentos + case-insensitive; los no
reconciliados se listan en `meta.warnings`.

### 6.6 Respuesta JSON

```json
{
  "candidatos": [
    {"nombre":"Javier Milei","pct":44.2,"pos":120,"neg":55,"neu":30,"menciones":205}
  ],
  "evidencia": [
    {"candidato":"Javier Milei","postura":"a_favor","cita":"...","post":{"...PostOut..."}}
  ],
  "comparacion": [
    {"candidato":"Javier Milei","redes_pct":44.2,
     "consultoras":[{"consultora":"Consultora X","pct":42.5,"gap":1.7}],
     "promedio_consultoras":41.3,"gap_promedio":2.9}
  ],
  "meta": {"total_posts":N,"posts_electorales":M,"disclaimer":"...","warnings":[...]}
}
```

### 6.7 Refactor: `gemini_client.py`

Se extrae de `normalizer.py` a un módulo compartido la **transporte de Gemini**:
cascada de modelos con fallback (429/503), rotación a `GEMINI_API_KEY_SECONDARY`,
y el parseo defensivo del JSON de respuesta. `normalizer.py` pasa a importar de
`gemini_client`; **su lógica no cambia**. `electoral.py` importa lo mismo. Es un
mover-funciones quirúrgico, cubierto por los tests existentes (deben seguir
verdes).

## 7. Frontend

Ruta nueva `frontend/app/boca-de-urna/page.tsx`. El monitor
(`app/page.tsx`) queda intacto; se agrega un link de navegación en
`layout.tsx` para saltar entre "Monitor" y "Boca de Urna".

Layout **C — dashboard denso de dos columnas**:

```
app/boca-de-urna/page.tsx        ← estado + orquestación del fetch
components/urna/
  ├── UrnaParamsBar.tsx          ← barra superior: keywords, país, fecha, upload CSV
  ├── DisclaimerBanner.tsx       ← encuadre "termómetro de redes, no encuesta"
  ├── SentimentBarChart.tsx      ← barras: sentimiento neto % por candidato (CSS puro)
  ├── EvidencePanel.tsx          ← citas + link al post que las respalda
  └── ComparisonTable.tsx        ← redes vs cada consultora + brecha (colores por gap)
lib/urnaApi.ts                   ← cliente HTTP de /api/boca-de-urna
```

Decisiones:

- **Sin librería de charts.** Barras con CSS puro (divs con `height`/`width`
  proporcional + `<title>` accesible). Evita sumar dependencias para 2 gráficos
  simples y respeta el estilo minimalista actual.
- **Branding SMATA** reutilizado: variables CSS `--smata-*`, tema claro/oscuro,
  `ThemeToggle`.
- **Upload CSV:** `<input type="file" accept=".csv">` → `FileReader.readAsText`
  → texto en el JSON. Se muestra nombre de archivo + filas parseadas OK antes de
  enviar.
- **Cold-start:** misma lógica de timeout + reintento + banner "servidor
  despertando" que `lib/api.ts` (Render free tier).
- **Tabla comparativa:** colorea la brecha (verde ±3pts, ámbar/rojo si diverge
  fuerte). Fila por candidato, columna por consultora, + "promedio consultoras"
  y "brecha promedio".

## 8. Errores, casos borde y testing

### 8.1 Casos borde

| Caso | Comportamiento |
|------|----------------|
| SerpAPI/Gemini sin cuota o caídos | 503 con mensaje claro |
| CSV mal formado | 400 si es irrecuperable; filas malas se saltean y van a `meta.warnings` |
| Sin CSV | Corre igual; solo ranking de redes, sin sección comparativa |
| Cero posts | 200 + "no se encontraron publicaciones" |
| Posts sin candidatos | 200 + "no se detectaron candidatos" |
| Candidato en CSV ausente en redes (o viceversa) | Se muestra con "—" y aviso en `meta.warnings` |
| Nombres que no reconcilian | Normalización sin acentos + case-insensitive; no matcheados a `meta.warnings` |
| Gemini JSON roto / no-espejo | Parseo defensivo reusado; si falla la cascada → 503 (no cachea vacío) |

### 8.2 Testing (pytest, siguiendo `test_normalizer.py`)

- `test_electoral.py` (Gemini + SerpAPI mockeados):
  - Agregación de sentimiento neto: `%`, `pos/neg/neu`, y fallback a volumen.
  - Normalización/reconciliación de nombres (alias, acentos, may/min).
  - Parseo de CSV: happy path, columnas faltantes, `%` no numérico, filas
    salteadas + warnings.
  - Comparación: `gap` por consultora y promedio.
  - Casos borde: sin CSV, cero posts, cero candidatos.
- `test_gemini_client.py`: smoke test del módulo extraído (cascada + rotación).
- Los tests existentes de `normalizer`/`fetcher` deben seguir **verdes** tras el
  refactor.

## 9. Encuadre y disclaimer (uso institucional)

- **Rótulo:** "Boca de Urna — Termómetro de Redes". Nunca "encuesta" ni
  "proyección de resultado".
- **Disclaimer fijo y visible** (barra ámbar, siempre presente): *"Este
  indicador refleja el clima de conversación en redes sociales sobre
  publicaciones públicas indexadas. No es una muestra representativa del
  electorado ni una proyección de resultado electoral. Sirve como termómetro
  direccional, complementario a las encuestas de consultoras."*
- **Transparencia del método a la vista:** se muestran `total_posts`,
  `posts_electorales` y, por candidato, `pos/neg/neu/menciones`. La evidencia
  enlaza a los posts reales.
- **La comparación se enmarca como "brecha", no como "quién tiene razón".** La
  precisión real solo se sabría con el resultado electoral (fuera de alcance).
- **`meta.warnings` visible** para exponer las limitaciones de cada corrida.

## 10. Archivos afectados

Nuevos:
- `backend/electoral.py`
- `backend/gemini_client.py`
- `backend/test_electoral.py`
- `backend/test_gemini_client.py`
- `frontend/app/boca-de-urna/page.tsx`
- `frontend/components/urna/UrnaParamsBar.tsx`
- `frontend/components/urna/DisclaimerBanner.tsx`
- `frontend/components/urna/SentimentBarChart.tsx`
- `frontend/components/urna/EvidencePanel.tsx`
- `frontend/components/urna/ComparisonTable.tsx`
- `frontend/lib/urnaApi.ts`

Modificados (quirúrgico):
- `backend/main.py` (endpoint nuevo + modelo Pydantic)
- `backend/normalizer.py` (importa de `gemini_client`; sin cambio de lógica)
- `frontend/app/layout.tsx` (link de navegación Monitor ↔ Boca de Urna)
- `README.md` (documentar la nueva sección y el endpoint)
