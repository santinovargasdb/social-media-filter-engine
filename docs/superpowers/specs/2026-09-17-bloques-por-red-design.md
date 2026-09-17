# Boca de Urna en bloques por red (X / Instagram / TikTok)

Fecha: 2026-09-17

## Contexto / problema

Hoy la urna hace un flujo único: busca todo mergeado y clasifica con UN prompt
genérico para todas las redes. El jefe pide separarlo en **bloques por red**, donde
cada bloque hace sus propias requests, con su propio contexto, y **corrobora sus
propios posts** — no un único "scraping" monolítico. Además quiere ~80 posts en el
candidato mejor posicionado (menos en los demás), con una cuota de 250 búsquedas/mes.

## Objetivo

1. Refactor a **tres bloques autónomos** (X, Instagram, TikTok): cada uno fetch +
   contexto + verificación propios, testeables por separado.
2. Mostrar el **desglose por red** por candidato (auditable).
3. Enfocar la cuota en los candidatos top para acercarse a ~80 en el mejor, dentro
   de ~45 búsquedas/análisis (≈5 corridas/mes con 250).

## Arquitectura

**`run_network_block(network, keywords, candidatos, date, country, budget, progress_cb)`**
(en `electoral.py`), uno por red:
1. **Requests**: arma sus specs para ESA red — general + por candidato — con la
   paginación de su presupuesto (`NETWORK_SEARCH_BUDGET[network]`). Fetch vía
   `normalizer.fetch_raw_posts(networks=[network], pages=...)`.
2. **Contexto**: clasifica con `analyze_posts_electoral(..., network_context=...)`,
   un prompt con contexto propio de la red (X = texto político directo; IG =
   foto/reel con epígrafe y hashtags; TikTok = video corto, texto breve/hashtags).
3. **Verificación**: corre SU clasificación sobre SUS posts (lotes secuenciales),
   corroborando cada post (electoral? candidato? postura?) de forma aislada.
4. Devuelve `{network, analysis, status:{buscados, encontrados, analizados}}`.

**`run_boca_de_urna`** pasa a: parse CSV + consultoras (igual) → correr los bloques
de las redes seleccionadas **secuencialmente** (para no gatillar el rate-limit de
Gemini; el async ya sacó la presión de tiempo) → **combinar**.

**Combine (`_merge_bloques`)**: agrega por candidato sumando pos/neg/neu/menciones de
todos los bloques y arma `por_red = {twitter, instagram, tiktok}` (menciones por red).
Reusa `aggregate_net_sentiment` por bloque y mergea; recalcula pct/pos_pct/etc sobre
los totales. La evidencia y la comparación con consultoras se arman sobre el conjunto.

## Presupuesto de búsqueda (editable, arranca conservador ~30-40)

`NETWORK_SEARCH_BUDGET` por red: `general_pages`, `top_n` (cuántos candidatos top
reciben `top_pages`), `rest_pages` (el resto; 0 = no se busca individual en esa red,
lo cubre la general). Arranque:
- twitter: general 1, top_n 3 × 2 páginas, rest 1  → ~17 búsquedas
- instagram: general 1, top_n 3 × 2 páginas, rest 0 → ~7
- tiktok: general 1, top_n 3 × 2 páginas, rest 0 → ~7

≈31 búsquedas/análisis (bajo el ~45 elegido → ~8 corridas/mes, más margen). Se puede
profundizar tras ver los rindes reales por red del primer análisis post-reset.

## Instrumentación (para tunear sin gastar cuota a ciegas)

`meta.bloques = [{red, buscados, encontrados, analizados}]` por red. Así el primer
análisis tras el reset muestra cuántos posts trae y clasifica CADA red — para saber
dónde profundizar y dónde Google no tiene material.

## Frontend

Mostrar el desglose por red: en la barra de cada candidato, "48 X · 20 IG · 12 TikTok"
(de `por_red`), y una línea de estado por bloque (buscados/encontrados) del `meta.bloques`.

## Límites conocidos

- **Cobertura de Google por red**: IG/TikTok están mucho menos indexados que X. Los
  ~80 en el top dependen de que Google los tenga; el presupuesto enfoca profundidad
  ahí, pero no se puede garantizar hasta medir (instrumentación por bloque).
- **No verificable ahora**: la cuota de SerpAPI está agotada este mes; se cubre con
  tests offline y una corrida de confirmación al resetear.

## Testing (TDD)

- `analyze_posts_electoral(network_context=...)`: el contexto entra al prompt; sin él,
  comportamiento idéntico (los tests actuales siguen verdes).
- `run_network_block`: arma specs de su red, clasifica solo lo suyo, devuelve status.
- `_merge_bloques`: suma por candidato y arma `por_red` correcto.
- `run_boca_de_urna`: corre bloques y combina; meta trae `bloques`; casos de 0 posts,
  upstream caído y parcial siguen andando.

## Plan de implementación

1. `analyze_posts_electoral(network_context="")` + prompt con contexto de red.
2. `NETWORK_SEARCH_BUDGET` + `run_network_block`.
3. `_merge_bloques` con `por_red`.
4. Rewire `run_boca_de_urna` a bloques + `meta.bloques`.
5. Frontend: desglose por red + estado por bloque.
6. Deploy; verificación en el reset con la instrumentación por bloque.
