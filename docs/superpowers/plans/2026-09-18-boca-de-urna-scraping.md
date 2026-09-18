# Plan: Boca de Urna vía scraping (SerpAPI queda solo para el Monitor)

Fecha: 2026-09-18

## Conclusión honesta (para decidir con el jefe)

"Scraping propio, gratis y confiable en Render free-tier" **no existe** para X/IG/TikTok
en 2026: un navegador headless (Playwright) no entra en los 512 MB, y las libs no
oficiales (twscrape/instaloader/TikTokApi) exigen proxies residenciales + cuentas +
mantenimiento constante (no es gratis "en serio", solo esconde el costo en infra/horas).

El camino realista y barato: **API de scraping HTTP de terceros** (pay-per-use), que
resuelve anti-bot/proxies/sesiones del lado del proveedor, corre con solo `requests`
(cero infra nueva) y **arranca con trial gratis**. Para X sale ~$0,15/1.000 tweets
(más barato que SerpAPI); un request trae hasta ~100 tweets → los ~80 del candidato top
son alcanzables. IG/TikTok son más caros/frágiles → después de validar X.

Cuello de botella que el scraping NO mueve: la clasificación con Gemini free-tier. No
traer más posts de los que Gemini alcanza a clasificar (`BLOCK_MAX_POSTS`).

## Diseño (encaja en la arquitectura de bloques existente)

Punto de swap único: dentro de `run_network_block` (electoral.py), la obtención de posts.

- **`backend/scrapers.py`** (nuevo): `scrape_network(network, termino, fecha_desde,
  keywords, accounts, country, pages) -> (posts, hubo_upstream)` — MISMA firma/retorno
  y MISMO shape de post que `normalizer.fetch_raw_posts` (reusa `_normalize_raw_post`,
  así la limpieza de URL/autor/purga TikTok es idéntica). X vía twitterapi.io
  (`advanced_search`, paginación por cursor). HTTP aislado (`_x_http`) y parseo aislado
  (`_tweet_to_src`) para testear con fixtures sin red. IG/TikTok: `NotImplementedError`
  por ahora (Fase 2/3).
- **Dispatcher + feature flag** (electoral.py): `_fetch_for_block` mira `_use_scraper_for`
  (env `URNA_FETCH_BACKEND=scraper` + `URNA_SCRAPER_NETWORKS` por red) y llama al scraper
  o a SerpAPI. Default = SerpAPI (cero cambio). Si una red prendida no está implementada,
  cae a SerpAPI.
- **NO cambia**: Gemini (`analyze_posts_electoral`), `por_red` (`_merge_bloques`), async
  (`jobs.py`), frontend, ni el Monitor de Medios (`fetch_posts`, siempre SerpAPI).

## Rollout

- **Fase 0 (hecha):** `scrapers.py` + adapter X + dispatcher + flag + tests, TODO
  apagado por defecto. Env vars documentadas en el README.
- **Fase 1 (requiere key):** sacar API key del trial de X (twitterapi.io, sin tarjeta),
  setear `X_SCRAPER_API_KEY`, prender `URNA_FETCH_BACKEND=scraper` + `URNA_SCRAPER_NETWORKS=twitter`.
  Verificar los nombres de campo de la API real contra `_tweet_to_src` (aislado). Medir
  volumen real con `meta.bloques` y ajustar `NETWORK_SEARCH_BUDGET["twitter"]`.
- **Fase 2:** Instagram (API de terceros, sin login). **Fase 3:** TikTok (opcional).

## Decisiones tomadas (con recomendación del planner)

1. API de terceros HTTP (no scraping propio) — única vía gratis-para-arrancar en el
   Render actual.
2. Proveedor X: twitterapi.io (trial $1 sin tarjeta) — validar al conectar.
3. X primero; IG/TikTok después de medir.
4. On-demand + caché agresivo (patrón de `_CACHE` existente).

## Riesgos

- Fragilidad estructural del scraping (mitigada: el flag permite volver a SerpAPI por
  red en segundos).
- Costo si no se cachea (opinión cambia lento → TTL largo).
- Volumen incierto hasta medir con la key real (`meta.bloques` lo instrumenta).
- ToS/legal: bajo con acceso público/deslogueado (lo que hacen las APIs de terceros).
