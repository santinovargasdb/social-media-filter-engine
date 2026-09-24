# Boca de Urna — Fase 4: TikTok en el scraper local (multi-red)

Fecha: 2026-09-24

## Contexto y decisiones cerradas

Fases 1-3 completas y en producción: el scraper local de X corre 2×/día por Task
Scheduler en la PC de la oficina y sube un snapshot a Supabase que el backend sirve en
modo `stored`. Diseño base: `2026-09-21-scraper-local-vision-design.md` (su fase 4
preveía "agregar IG y TikTok").

Decisiones tomadas con el usuario (2026-09-24):

- **TikTok primero; Instagram después** (fase aparte). TikTok se parece más al patrón
  que ya anda (búsqueda con cards que muestran texto); IG es una grilla visual hostil
  que merece su propio diseño con lo aprendido acá.
- **Contenido: búsqueda + comentarios top.** Las cards de búsqueda dan la opinión de
  quien publica; los comentarios de los videos más relevantes dan la de la audiencia
  (la "boca de urna" más pura).
- **Cuentas: pool de descartables como X.** 2-3 cuentas de TikTok logueadas a mano una
  vez; el scraper rota si se queman. Los comentarios casi siempre exigen sesión.
- **Cuota Gemini (restricción dura, medida el 2026-09-23):** una corrida de X son ~36
  llamadas de visión y dos corridas completas casi agotan el free-tier diario (con
  rotación de key incluida). Por eso: comentarios **solo para los ~5 punteros** (2
  videos c/u) y TikTok corre **solo en la corrida de las 16:00**. TikTok agrega
  12×3 + 5×2×2 = **56 llamadas**; el día queda en ~128, bajo el techo medido.
- **Arquitectura: módulo por red con contrato común** (elegida sobre "duplicar
  browser_tiktok.py" y "un browser.py parametrizado"): el flujo de comentarios de
  TikTok no se expresa con selectores de config, y con IG en el horizonte la
  duplicación saldría cara.

## Arquitectura

```
run.py — itera las redes ACTIVAS de la corrida (--redes / config)
   por red × candidato:
     redes/twitter.py  → búsqueda X (lógica actual movida desde browser.py)
     redes/tiktok.py   → búsqueda + (punteros) comentarios de videos top
        ambos usan browser.py (común: scroll+captura, esperas, SesionInvalidaError)
        y accounts.py (pool POR RED, rotación una vez por candidato)
     vision.read_capture(captura, red, contexto) — paceado, cascada + rotación de keys
     dedup por red — clave (red, autor, hash del texto)
   1 BLOQUE por red → electoral._merge_bloques(bloques) → payload con por_red
   → store.write_snapshot (UN snapshot por corrida, como hoy)
```

**Backend y frontend: cero cambios.** `_merge_bloques` ya fusiona varios bloques y
calcula `por_red`; el frontend ya lo muestra.

## Componentes

### `scraper_local/redes/` (NUEVO paquete)

Contrato por red (módulo por archivo):

- `capturar(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings) -> list[dict]` —
  retorna `[{"ruta": Path, "contexto": str}, …]`; lanza `browser.SesionInvalidaError`
  ante sesión muerta o challenge.
- Constantes de login para `accounts.py`: `LOGIN_URL` y detección de login terminado
  `login_completado(url: str) -> bool` (X: llegar a `/home`; TikTok: salir de `/login`).

**`redes/twitter.py`** — mover `capturar_busqueda` y sus constantes (`X_SEARCH_URL`,
`SELECTOR_FEED`, `MARCAS_SESION_MUERTA`) desde `browser.py`, sin cambios de
comportamiento.

**`redes/tiktok.py`** — por candidato:

1. **Búsqueda**: `tiktok.com/search?q=<candidato>`, espera las cards, N capturas
   scrolleando (reusa `browser.capturar_pagina`).
2. **Comentarios** (solo si el candidato está en `candidatos_comentarios`): extrae de
   la página de búsqueda los links de los primeros `videos_comentarios` videos, abre
   cada uno, espera el panel de comentarios y captura el panel scrolleándolo (el
   panel, no la ventana). Prefijo `-comentarios-` en el nombre del PNG.

Selectores/marcas reales (cards, panel, captcha) como constantes `# TUNEAR` arriba del
módulo — se ajustan en la PC de la oficina, como se hizo con X.

### `browser.py` (queda lo común)

`capturar_pagina`, `esperar_aleatorio`, `SesionInvalidaError`, launch con
`--disable-blink-features=AutomationControlled`. Gana un helper para capturar
scrolleando un ELEMENTO (el panel de comentarios) además del viewport.

### `accounts.py` (pool por red, sin migración)

- `cargar_pool(red)` / `guardar_pool(pool, red)` / `ruta_sesion(alias, red)`.
- **Twitter conserva sus archivos actuales** (`accounts.json`,
  `.sesiones/<alias>.json`): las sesiones ya logueadas siguen valiendo tal cual.
- TikTok usa `accounts-tiktok.json` y `.sesiones/tiktok-<alias>.json` (git-ignored,
  mismo patrón).
- CLI: `python accounts.py login <alias> [--red tiktok]` (default `twitter`) y
  `estado [--red ...]`. Mismo login manual con detección automática de éxito.

### `vision.py` (cambio mínimo)

Parámetro opcional `contexto` que se antepone al prompt. Para capturas de comentarios:
"esto es el panel de comentarios de un video sobre <candidato>; cada comentario cuenta
como una publicación". Reglas de lectura y clasificación direccional: idénticas.

### `dedup.py`

La clave incluye la red: `(red, autor, hash del texto)`. Duplicados entre capturas
solapadas del mismo panel se fusionan; el mismo texto en redes distintas, no.

### `run.py` + `config.json` + `run.ps1`

- `config.json` pasa de `"red": "twitter"` a:

```json
{ "redes": {
    "twitter": { "scrolls_por_candidato": 3 },
    "tiktok":  { "scrolls_por_candidato": 3,
                 "videos_comentarios": 2,
                 "scrolls_comentarios": 2,
                 "candidatos_comentarios": ["Javier Milei", "Axel Kicillof",
                   "Sergio Massa", "Patricia Bullrich", "Cristina Fernández de Kirchner"] } } }
```

  Las claves globales actuales (`viewport`, `headless`, `espera_entre_scrolls`,
  `espera_entre_candidatos`, `min_posts_electorales`, `conservar_corridas`,
  `candidatos`) siguen en el nivel superior y aplican a todas las redes.
  (`cargar_config` traduce el formato legacy `"red"` si lo encuentra, para no romper
  configs locales.)
- `run.py --redes twitter,tiktok` acota la corrida a esas redes (default: todas las
  del config). Itera redes → bloque por red → `armar_payload(bloques)`.
- `run.ps1` gana el parámetro `-Redes` y lo pasa a `run.py`.
- Task Scheduler: la tarea de las **10:00** pasa a `-Redes twitter`; la de las
  **16:00**, `-Redes twitter,tiktok`.

## Manejo de errores (degradación por partes, misma filosofía)

- Challenge/captcha/redirect a login en TikTok → `SesionInvalidaError` → rota la
  cuenta UNA vez, marca quemada la anterior. Sin cuentas activas: TikTok queda sin
  capturar (warning) y la corrida sigue con X.
- Falla un video o no aparece el panel → warning y sigue con el próximo
  video/candidato (la búsqueda de ese candidato ya está capturada).
- Regla de seguridad global sin cambios: pocos posts electorales o subida fallida →
  exit 1 y NO se pisa el snapshot anterior.
- Si TikTok entero falla, el snapshot sube igual solo con X; el bloque de TikTok queda
  en `meta.bloques` con sus errores y warnings visibles.

## Cuota y tiempos

- TikTok: 56 llamadas de visión por corrida (36 búsqueda + 20 comentarios). Día
  completo ~128 llamadas (~36 a las 10:00 + ~92 a las 16:00) — bajo el techo medido el
  2026-09-23, con cascada de modelos + key secundaria de paracaídas.
- Corrida de las 16:00: ~50-70 min (visión paceada a `VISION_MIN_INTERVAL` +
  navegación TikTok con esperas humanas). Es batch: se tolera.

## Testing

- Todo con mocks; la suite corre sin Playwright (imports diferidos, como hoy).
- `testdata/` suma búsqueda falsa de TikTok y panel falso de comentarios (HTML
  estático) para probar scroll/captura/extracción de links en local.
- Unit tests: URL de búsqueda, extracción de links de video del HTML falso, pools por
  red (twitter legacy intacto + tiktok), dedup con red en la clave, `run.py` con 2
  bloques y payload fusionado con `por_red` correcto, traducción del config legacy.
- Verificación real (cuentas + selectores vivos): a mano en la PC de la oficina.

## Límites conocidos (honestos)

- El anti-bot de TikTok es más agresivo que el de X (captcha con puzzle, muros de
  login variables): las cuentas pueden quemarse más rápido; hay que reponerlas.
- Crear cuentas descartables de TikTok a veces pide teléfono (setup manual del
  usuario; puede ser la parte más molesta).
- El panel de comentarios cambia de DOM seguido: es el selector con más probabilidad
  de romperse cada tanto.

## Fuera de alcance de esta fase

- Instagram (fase propia, con lo aprendido acá).
- Cambios de backend/frontend (no hacen falta).
- Tuning fino de selectores en CI (imposible: se hace en vivo en la oficina).
