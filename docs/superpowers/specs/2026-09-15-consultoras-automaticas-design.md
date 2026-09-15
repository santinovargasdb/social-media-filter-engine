# Boca de Urna — Consultoras automáticas (con fuente)

**Fecha:** 2026-09-15
**Estado:** diseño aprobado (pendiente revisión del spec)

## Problema

Hoy la comparación "Redes vs consultoras" de la Boca de Urna se llena **subiendo un CSV
a mano** con los datos de las encuestadoras. Es tedioso y hay que conseguir los números
por afuera. Se busca que la app **traiga automáticamente** los datos de un conjunto fijo
de consultoras y los muestre **con su fuente**, sin carga manual.

## Requisito central: FIDELIDAD, no exactitud

El único compromiso de la app es ser un **espejo fiel** de lo que publica cada consultora,
con su fuente al lado. Si una consultora tiene un dato erróneo o difiere de otras, eso es
información válida (dispersión entre encuestadoras), no un bug de la app. Por eso **cada
número mostrado enlaza a su fuente**: si la extracción automática leyó mal un valor, el
usuario lo detecta de un clic.

## Alcance

- **Incluye:** fetch automático de datos de 5 consultoras fijas (editables), extracción
  estructurada con IA, enlace a la fuente por dato, integración en la tabla de comparación,
  y conservar la carga por CSV como respaldo/override.
- **No incluye (v1):** lectura de encuestas publicadas SOLO como infografía/imagen (haría
  falta visión); un agregador estructurado tipo tabla de Wikipedia 2027 (aún no existe —
  ver "Evolución futura").

## Consultoras por defecto (editable)

Lista fija `CONSULTORAS_DEFAULT` (en el backend, editable como `CANDIDATOS_DEFAULT`),
elegida por prestigio metodológico y **equilibrio de espectro** (percepción de sesgo es
pública y debatida, ninguna se declara partidaria):

1. **Opinaia** — neutral / profesional
2. **Poliarquía** — neutral / establishment
3. **Management & Fit** — neutral / centro
4. **CB Consultora** — suele medir mejor al oficialismo/LLA
5. **Zuban Córdoba** — suele medir mejor al peronismo/kirchnerismo

(3 neutrales + 1 de cada polo, para que ningún lado domine.)

## Arquitectura

### Backend

**Módulo nuevo `backend/pollsters.py`** (fuera de `electoral.py`, que ya es grande y tiene
otra responsabilidad). Única función pública:

```
fetch_pollster_rows(fecha_desde, country) -> tuple[list[dict], list[str]]
```

Devuelve `(rows, warnings)` donde cada `row` tiene el MISMO shape que produce
`parse_pollster_csv` MÁS los campos de fuente:

```
{ "consultora", "fecha", "candidato", "porcentaje",
  "fuente_url", "fuente_titulo" }
```

Flujo interno (por cada consultora de `CONSULTORAS_DEFAULT`, en paralelo):
1. **Buscar** con SerpAPI (Google, sin filtro `site:`) una query tipo
   `"<consultora>" encuesta intención de voto <ELECCION_LABEL>`, donde `ELECCION_LABEL` es
   una constante editable (ej. `"presidencial 2027"`) para no hardcodear el año en la
   lógica. Tomar los 1-2 resultados orgánicos más recientes (title, snippet, url, date).
2. **Traer el texto del artículo** (HTTP GET + extracción best-effort de texto legible).
   Si falla (paywall/anti-bot/timeout), caer al `snippet` de SerpAPI.
3. **Extraer con Gemini** (transporte compartido `gemini_client.run_with_rotation`, en
   lotes) las filas `{consultora, fecha, candidato, porcentaje}` que aparezcan de forma
   EXPLÍCITA en el texto, atadas a la `url` de esa nota. Reglas:
   - Solo candidatos-persona; excluir "en blanco", "indeciso", "no sabe", "ninguno".
   - Nombre canónico completo (reusa criterio del prompt electoral).
   - Si el texto no trae números claros de esa consultora, devolver `[]` (no inventar).
4. **Dedup**: por `(consultora, candidato)` conservar la fila de **fecha más reciente**.

No habla con SerpAPI ni Gemini "a mano": reusa `fetcher`/`gemini_client`. Stateless salvo
el cache (abajo).

**Fetcher:** `fetcher.search_serpapi` hoy siempre agrega `site:<red>`. Se agrega un modo
"web general" (parámetro para omitir el filtro `site:`) o una función hermana
`search_serpapi_web(...)`. Cambio acotado y aislado en la Capa 2.

**Cache:** las encuestas cambian lento. Cachear `fetch_pollster_rows` con TTL largo
(6–24 h), key = `(tuple(consultoras), año, country)`. Reduce fuerte el consumo de cuota en
análisis repetidos.

**Integración en `run_boca_de_urna`:**
- La request suma un flag `auto_consultoras: bool` (default false para compat).
- Si `auto_consultoras` y NO se subió CSV → `pollster_rows = fetch_pollster_rows(...)`.
- Si hay CSV → se usa el CSV (y, si además `auto_consultoras`, se MERGEA: el CSV pisa al
  auto en conflicto `(consultora, candidato)` porque es dato vetado a mano).
- El resto del flujo (aggregate, evidencia, `compare_vs_pollsters`) no cambia.

**`compare_vs_pollsters`:** hoy emite `consultoras: [{consultora, pct, gap}]`. Se extiende
para arrastrar `fuente_url`, `fuente_titulo` y `fecha` por consultora hasta el payload.

### Frontend

- **`UrnaParamsBar`**: toggle "⟳ Traer consultoras automáticamente" que setea
  `auto_consultoras` en la request.
- **`ComparisonTable`**: cada celda de consultora muestra el `%` con un link ↗ a la fuente
  y tooltip con `fecha` + `fuente_titulo`. Nota al pie: *"Datos extraídos automáticamente
  de fuentes públicas — verificá en la fuente."*
- **Tipos** (`urnaApi.ts`): `UrnaConsultora` suma `fuente_url?`, `fuente_titulo?`, `fecha?`.
- El CSV manual sigue disponible tal cual.

## Flujo de datos

```
toggle "auto" ─▶ POST /api/boca-de-urna { auto_consultoras: true }
     ▼
run_boca_de_urna
     ├─ (social, ya existente) ─▶ candidatos + evidencia
     └─ pollsters.fetch_pollster_rows ─▶ filas con fuente ──┐
                                                            ▼
                              compare_vs_pollsters(candidatos, filas)
                                                            ▼
                        payload.comparacion (con fuente por consultora)
                                                            ▼
                       ComparisonTable: % + link a la fuente
```

## Manejo de errores

- SerpAPI/Gemini caídos o sin resultados para una consultora → esa consultora se saltea
  con un warning; el análisis NO se rompe.
- Fetch del artículo falla (paywall) → cae al snippet; si tampoco alcanza, se saltea.
- Nada encontrado en ninguna consultora → `comparacion` con solo `redes_pct`, más un
  warning "no se encontraron encuestas recientes de las consultoras seguidas".
- Todo upstream caído + sin social → 503 como hoy.

## Performance / cuota

- 5 consultoras → ~5 búsquedas SerpAPI + ~5 fetches de artículo + extracción Gemini en
  lotes, TODO en paralelo (ThreadPoolExecutor) y bajo el presupuesto de 120 s.
- El cache de TTL largo hace que la mayoría de los análisis no re-consulten.
- Es costo ADITIVO al análisis social; se mide en vivo tras implementar y se ajustan topes
  si hace falta (mismo criterio que se usó con las búsquedas por candidato).

## Testing

- `pollsters.py` con SerpAPI + Gemini mockeados:
  - extracción arma el shape correcto con `fuente_url`;
  - dedup por `(consultora, candidato)` conserva la fecha más reciente;
  - excluye opciones no-candidato;
  - falla de una consultora no tumba al resto (resultados parciales);
  - fallback de artículo→snippet.
- `compare_vs_pollsters`: los campos de fuente fluyen al payload.
- `run_boca_de_urna`: con `auto_consultoras` y `fetch_pollster_rows` mockeado, la
  comparación se llena; el CSV pisa al auto en conflicto.
- Frontend: typecheck (`tsc --noEmit`).

## Evolución futura (fuera de v1)

- Cuando exista un agregador estructurado (p. ej. tabla de Wikipedia de encuestas 2027),
  parsearlo da **máxima fidelidad** (tabla, sin IA adivinando) y se puede priorizar sobre
  la extracción de noticias. El módulo `pollsters.py` queda como punto único donde
  enchufar esa fuente.
- Lectura de infografías con un modelo de visión.

## Límites honestos (v1)

- La extracción desde texto de noticias no es perfecta: un número mal leído es
  responsabilidad de la app, pero es **verificable** por el link a la fuente.
- Encuestas publicadas solo como imagen no se leen en v1.
- La cobertura es "lo que aparezca en las noticias recientes" de esas 5 consultoras.
