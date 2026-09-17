# Boca de Urna asíncrona (romper el límite de 120s)

Fecha: 2026-09-17

## Contexto / problema

La Boca de Urna corre bajo el timeout de 120s del frontend (`urnaApi.ts`). Para no
pasarse, el corpus está capado en `ELECTORAL_MAX_POSTS = 40` y cada término aporta
`POSTS_PER_TERM = 3`. Resultado: aún con un plazo de 9 meses, el candidato más
mencionado (Milei) junta ~11 menciones — inaceptable para el clima político real.

El techo de 40 existe SOLO por el timeout de 120s. Si sacamos ese límite, podemos
procesar un corpus mucho más grande (varias tandas de Gemini) sin romper nada.

## Objetivo

Convertir el análisis en un **trabajo en segundo plano** con progreso consultable,
para procesar un corpus grande (~200 posts) sin el techo de 120s, y subir los topes
de volumen para que cada candidato junte muchas más menciones.

## Arquitectura

Patrón job + polling, **sin base de datos ni infra nueva** (encaja en el free-tier de
Render: un worker, se duerme por inactividad):

- **`POST /api/boca-de-urna/start`**: valida el request (mismo `BocaDeUrnaRequest`),
  crea un `job_id` (uuid4), lanza un **hilo daemon** que corre `run_boca_de_urna` y
  escribe estado/resultado en un almacén en memoria. Devuelve `{ "job_id": ... }` al
  instante (sin esperar el análisis).
- **`GET /api/boca-de-urna/status/{job_id}`**: devuelve
  `{ state: "running"|"done"|"error", progress: { phase, pct }, result?, error? }`.
- **`jobs.py`** (módulo nuevo): almacén en memoria `dict[job_id] -> job`, protegido con
  un `Lock`, con TTL (los trabajos viejos se barren). API: `create`, `set_progress`,
  `set_result`, `set_error`, `get`.
- **Frontend**: al "Analizar" llama a `start`, guarda el `job_id` y **consulta cada ~2s**
  el `status`, mostrando una barra/estado ("Buscando publicaciones…", "Analizando N
  publicaciones…"). Cuando `state==="done"` renderiza el resultado; `error` muestra el
  mensaje. Mientras consulta, Render no se duerme y el trabajo termina.
- El endpoint viejo `POST /api/boca-de-urna` se deja funcionando (compat), pero el
  frontend pasa al flujo async.

**Progreso (grueso, YAGNI):** `run_boca_de_urna` acepta un callback opcional
`progress_cb(phase: str, pct: float)` que llama en los hitos: consultoras → búsqueda
→ clasificación (con el total de posts) → armado. El hilo pasa un callback que escribe
en el job. Sin callback, no-op (así los tests no cambian).

## Cambios de volumen (constantes tuneables, arranque "Moderado")

- `ELECTORAL_MAX_POSTS`: 40 → **200**.
- `POSTS_PER_TERM`: 3 → **15** (cada candidato aporta mucho más).
- Paginación en la urna: `fetch_raw_posts` acepta `pages` y la urna usa **2 páginas**
  (`URNA_SERP_PAGES = 2`) — reusa la paginación de `search_serpapi` ya construida.
- `CANDIDATE_NETWORKS` se mantiene en `("twitter",)`: la búsqueda POR candidato va a X
  (donde vive la conversación política y para acotar cuota); la búsqueda GENERAL sí
  pega a las 3 redes. Con 2 páginas: general 3 redes × 2 + 13 candidatos × X × 2 ≈
  **~32 búsquedas SerpAPI** por análisis (vs ~9). Subir per-candidato a 3 redes queda
  como perilla "agresivo" a futuro.
- `ELECTORAL_BATCH_CONCURRENCY`: 2 → **3** (más lotes en paralelo; ya no hay presión de
  120s, pero se mantiene bajo para no gatillar el rate-limit del free-tier de Gemini).
- En la primera corrida real se **mide y reporta** cuántos posts únicos trae la búsqueda
  (para saber si el techo pasa a ser la cobertura de Google y no la config).

## Manejo de errores

- Excepción en el hilo → `state="error"` con el mensaje (incluye
  `UpstreamUnavailableError` de Gemini/SerpAPI caído).
- Si Render se reinicia con un trabajo a medias (raro mientras se consulta), el job se
  pierde; el frontend, ante un `job_id` inexistente (404), ofrece reintentar.

## Límites conocidos (honestos)

- **Cobertura de Google:** SerpAPI busca en Google (`site:x.com …`), que indexa solo una
  fracción de los posts sociales. Subir topes no crea posts que Google no tenga. Para
  volumen tipo "30/mes por candidato" garantizado haría falta una fuente paga (X API).
- **Duración:** ~200 posts en lotes de 20 ⇒ ~10 llamadas a Gemini; con el free-tier y
  su rate-limit, el análisis puede tardar ~2-4 min. Es esperado (por eso es async).

## Testing (TDD)

- `jobs.py`: crear devuelve id único; set/get de progreso, resultado y error; vencimiento
  por TTL.
- Endpoints (`test_main_urna.py`): `start` devuelve job_id y arranca el trabajo (análisis
  mockeado, sin red); `status` refleja running → done con el resultado; done propaga
  errores como `state="error"`.
- `fetch_raw_posts`: pasa `pages` a `search_serpapi`.
- `run_boca_de_urna`: llama al `progress_cb` en los hitos (con un callback espía).
- La lógica de análisis existente no cambia: la suite actual debe seguir verde.

## Plan de implementación (orden)

1. `jobs.py` + tests.
2. `fetch_raw_posts(pages=…)` + subir constantes de volumen.
3. `run_boca_de_urna(progress_cb=…)`.
4. Endpoints `start` / `status` + tests.
5. Frontend: cliente async + polling + barra de progreso.
6. Deploy y verificación en vivo (medir volumen real por candidato).
