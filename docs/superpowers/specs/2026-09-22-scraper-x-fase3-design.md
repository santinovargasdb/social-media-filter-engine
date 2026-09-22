# Boca de Urna — Fase 3: scraper de X (Playwright + cuentas rotativas + snapshot)

Fecha: 2026-09-22
Diseño padre: `2026-09-21-scraper-local-vision-design.md` (Fases 1 y 2 hechas).

## Alcance

El scraper local completo para X: `accounts.py` (cuentas descartables con login
manual), `browser.py` (capturas de la búsqueda con Playwright), `dedup.py` y
`run.py` (orquesta y sube el snapshot a Supabase). Se entrega estructura + lógica
+ tests con mocks; los selectores/login de X se tunean a mano en la PC de la
oficina (no testeable en CI). IG/TikTok quedan para Fase 4.

## Decisiones cerradas

- **Login MANUAL una vez por cuenta** (elegido por el usuario): `accounts.py login`
  abre navegador visible, el operador loguea a mano y las cookies quedan
  persistidas. Sin passwords guardados, sin login automatizado.
- **Reuso de la agregación del backend**: `run.py` arma UN bloque con la misma
  forma que usa `electoral` y llama `electoral._merge_bloques` +
  `electoral.build_evidence` + `electoral.compare_vs_pollsters(candidatos, [])`.
  El snapshot sale con forma idéntica a la del análisis vivo.
- **Captura por viewport + scroll** (no full-page): el feed de X es virtualizado
  (desmonta posts al scrollear); N capturas de pantalla por candidato, igual que
  valida el smoke test de la Fase 2.
- **Si la corrida no junta ni un post electoral, NO se pisa el snapshot anterior.**

## Componentes

### 1. `scraper_local/accounts.py`

- **Pool** en `scraper_local/accounts.json` (GIT-IGNORED; plantilla
  `accounts.example.json` commiteada):
  ```json
  {"cuentas": [{"alias": "cuenta1", "estado": "activa", "ultima_vez": "", "notas": ""}]}
  ```
  `estado`: `"activa"` | `"quemada"`. Cookies (storage_state de Playwright) en
  `scraper_local/.sesiones/<alias>.json` (GIT-IGNORED).
- **API**: `cargar_pool()`, `guardar_pool(pool)`, `proxima_cuenta(pool)` (la
  activa con `ultima_vez` más vieja; `None` si no quedan),
  `registrar_uso(pool, alias)` (timestamp ISO), `marcar_quemada(pool, alias)`,
  `ruta_sesion(alias)`.
- **CLI**: `python accounts.py login <alias>` — abre Chromium VISIBLE en
  `https://x.com/login`, el operador loguea a mano y aprieta Enter en la consola;
  se guarda el storage_state y la cuenta queda `activa`. `python accounts.py estado`
  lista el pool con estado y última vez.

### 2. `scraper_local/browser.py`

- `capturar_busqueda(sesion, termino, scrolls, viewport, headless, esperas, carpeta) -> list[Path]`:
  abre Chromium con el storage_state, navega a
  `https://x.com/search?q=<termino + " lang:es" urlencoded>&src=typed_query&f=live`,
  espera el feed (selector `article`), captura el viewport, scrollea ~90% de la
  pantalla con espera aleatoria (rango `esperas`, default 2-5s) y repite hasta
  juntar `scrolls` capturas. Devuelve las rutas de los PNG.
- **Detección de sesión muerta/challenge** → `SesionInvalidaError`: la URL terminó
  en `/login`, `/account/access` o `/i/flow`, o el feed (`article`) no apareció en
  el timeout. `run.py` decide la rotación (browser no toca el pool).
- **Capturas de debug**: se guardan en `scraper_local/capturas/<YYYYMMDD-HHMM>/`
  (GIT-IGNORED); `run.py` conserva las últimas `conservar_corridas` carpetas.
- **CLI dry-run** (prueba la maquinaria sin login ni X):
  `python browser.py --url <URL> [--scrolls N] [--visible] [--out carpeta]` —
  mismas capturas + scroll sobre cualquier URL. Se verifica en vivo contra el
  feed falso de `testdata/` servido por localhost.

### 3. `scraper_local/dedup.py`

- `dedup_posts(posts) -> list[dict]`: conserva la primera aparición por clave
  `(autor normalizado, md5 de texto normalizado)`. Normalización: autor sin `@`
  inicial, lowercase; texto lowercase, espacios colapsados, primeros 200 chars.
  Posts sin autor Y sin texto no se deduplican entre sí (clave con el índice).

### 4. `scraper_local/run.py`

- **CLI**: `python run.py [--dry-run] [--config ruta]`. `--dry-run`: no sube a
  Supabase ni borra capturas viejas; imprime el resumen.
- **Config** en `scraper_local/config.json` (commiteado, editable en la oficina):
  ```json
  {"red": "twitter", "candidatos": null, "scrolls_por_candidato": 3,
   "espera_entre_scrolls": [2, 5], "espera_entre_candidatos": [20, 40],
   "viewport": [950, 1300], "headless": true, "min_posts_electorales": 1,
   "conservar_corridas": 3}
  ```
  `candidatos: null` → usa `electoral.CANDIDATOS_DEFAULT`.
- **Flujo**: cargar config y pool → por candidato: capturas con la cuenta activa
  (ante `SesionInvalidaError`: marcar quemada, tomar la próxima y reintentar UNA
  vez ese candidato; sin cuentas → warning y se sigue con lo juntado) → por
  captura: `vision.read_capture(path, red)` (paceo de la Fase 2; `None` = error,
  se cuenta y sigue) → `dedup.dedup_posts` global → mapear a
  `posts_by_id`/`analysis` (ids `Post_N`; post: `network`, `author`,
  `author_url` = `https://x.com/<handle>` si el autor empieza con `@` sino `""`,
  `text`, `post_url": ""`, `date` = fecha relativa tal cual) → bloque
  `{"network": red, "posts_by_id": ..., "analysis": ..., "status": {"red", "busquedas",
  "crudos", "errores", "encontrados", "analizados"}}` → `_merge_bloques([bloque])`
  + `build_evidence` + `compare_vs_pollsters(candidatos, [])` → payload
  `{candidatos, evidencia, comparacion, meta}` con `meta` =
  `{total_posts, posts_electorales, analizados, bloques, disclaimer, warnings}`
  (disclaimer = `electoral.DISCLAIMER`; warnings acumulados: cuentas quemadas,
  capturas fallidas, baja confianza).
- **Subida**: si `posts_electorales >= min_posts_electorales` →
  `store.write_snapshot(payload, generado_en_iso_utc)`; si no (o si la subida
  falla) → NO se sube, log del motivo y exit code 1 (para que Task Scheduler
  registre el fallo). El snapshot anterior queda intacto.
- **Logs**: consola + archivo `scraper_local/logs/run-<YYYYMMDD-HHMM>.log`
  (GIT-IGNORED), módulo `logging`.

### 5. Setup de la PC de la oficina

- `scraper_local/requirements.txt`: `playwright` + `requests` (el backend NO se
  toca; su requirements.txt queda igual).
- `scraper_local/run.ps1`: activa el venv local y corre `python run.py` (es lo
  que apunta Task Scheduler).
- Sección nueva en `scraper_local/README.md`: crear venv, `pip install -r
  requirements.txt`, `playwright install chromium`, cargar cuentas
  (`accounts.py login`), `SUPABASE_URL`/`SUPABASE_KEY` (service role) +
  `GEMINI_API_KEY` en variables de entorno o `.env` local, y el alta en Task
  Scheduler (2-3×/día).
- `.gitignore`: `scraper_local/accounts.json`, `scraper_local/.sesiones/`,
  `scraper_local/capturas/`, `scraper_local/logs/`.

## Interfaces reusadas (ya existentes, no se modifican)

- `vision.read_capture(image, red) -> list[dict] | None` (Fase 2).
- `electoral._merge_bloques(bloques) -> (candidatos, baja_confianza, posts_by_id_total, analysis_total)`.
- `electoral.build_evidence(analysis, posts_by_id, por_candidato=5) -> list[dict]`.
- `electoral.compare_vs_pollsters(candidatos, []) -> (comparacion, warnings)`.
- `electoral.CANDIDATOS_DEFAULT`, `electoral.DISCLAIMER`, `electoral.CONF_MIN`.
- `store.write_snapshot(payload, generado_en) -> bool`.

## Testing

- **Unit con mocks (CI)**: `test_accounts.py` (pool, rotación por `ultima_vez`,
  marcado, persistencia en tmp_path), `test_dedup.py` (claves, normalización,
  primera aparición), `test_run.py` (orquestación con browser/vision/store
  mockeados: mapping al shape de electoral, rotación ante `SesionInvalidaError`,
  no-subida con 0 electorales, exit codes, dry-run). `browser.py` NO tiene unit
  tests de selectores (dependen de X vivo).
- **Verificación en vivo local (la hace el orquestador)**: dry-run de
  `browser.py` contra el feed falso servido en localhost — valida navegación,
  espera, scroll y capturas reales de Playwright sin tocar X.
- **Verificación en oficina (manual, fuera de este repo-work)**: login real,
  corrida completa contra X, tuning de selectores/tiempos.

## Límites conocidos (heredados del diseño padre)

Cuentas que se queman (IP fija, sin proxies), selectores de X que cambian,
Playwright lento en el Celeron. El batch tolera todo esto: corre 2-3×/día y la
app siempre muestra el último snapshot bueno.
