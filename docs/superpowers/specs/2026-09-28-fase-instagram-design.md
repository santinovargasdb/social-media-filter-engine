# Boca de Urna — Fase Instagram: scraper local (sidebar search + posts top)

Fecha: 2026-09-28

## Contexto y decisiones cerradas

Fases 1-4 completas y en producción: X corre 2×/día, TikTok se suma a las 16:00.
El scraper multi-red ya tiene el paquete `redes/` con contrato común y `run.py` que
itera bloques por red. Instagram se integra como un tercer módulo en ese paquete,
sin cambios en backend ni frontend.

Decisiones tomadas con el usuario (2026-09-28):

- **Contenido: grilla de búsqueda + posts individuales para punteros.** La grilla se
  usa solo para navegar y extraer links (sin Gemini sobre thumbnails — no hay texto).
  Para los 5 punteros se abren los primeros 3 posts individualmente y se captura la
  pantalla completa (caption + primeros comentarios visibles).
- **Posts individuales: solo los 5 punteros** (Milei, Kicillof, Massa, Bullrich, CFK).
  Los demás candidatos no generan llamadas a Gemini desde IG.
- **Scheduling: solo la corrida de las 16:00** (con TikTok). La de las 10:00 sigue
  siendo solo X.
- **Navegación: sidebar search** (no URL directa). Lección de TikTok: la URL directa
  activa el anti-bot; el flujo humano (home → lupa sidebar → tipear → pestaña Posts)
  es más robusto.

## Arquitectura

```
run.py — itera las redes ACTIVAS (--redes / config)
   redes/twitter.py   — búsqueda X
   redes/tiktok.py    — búsqueda + comentarios punteros
   redes/instagram.py — sidebar search → links → posts punteros  ← NUEVO
      todos usan browser.py (scroll, capturas, SesionInvalidaError)
      y accounts.py  (pool por red, rotación una vez por candidato)
   vision.read_capture(captura, red, contexto)
   dedup por red → 1 bloque por red → _merge_bloques → snapshot Supabase
```

Backend y frontend: **cero cambios**.

## Componentes

### `scraper_local/redes/instagram.py` (NUEVO)

Contrato:

```python
capturar(sesion: Path, termino: str, cfg: dict, cfg_red: dict,
         carpeta: Path, prefijo: str, warnings: list[str]) -> list[dict]
# Retorna [{"ruta": Path, "contexto": str}, ...]
# SesionInvalidaError si la cuenta está muerta o hay challenge.
```

Constantes de login (para `accounts.py`):

```python
LOGIN_URL = "https://www.instagram.com/accounts/login/"

def login_completado(url: str) -> bool:
    return "/accounts/login/" not in url and "/challenge/" not in url
```

Selectores (todos marcados `# TUNEAR`):

```python
SELECTOR_SEARCH_ICON = "[aria-label='Buscar']"     # icono lupa sidebar  # TUNEAR
SELECTOR_SEARCH_INPUT = "input[placeholder*='Buscar']"                   # TUNEAR
SELECTOR_TAB_POSTS    = "text=Posts"                                      # TUNEAR
SELECTOR_POST_GRID    = "article"                  # contenedor de grilla # TUNEAR
SELECTOR_POST_LINKS   = "a[href*='/p/']"           # links a posts        # TUNEAR
SELECTOR_CHALLENGE    = "[data-testid*='challenge'], [class*='challenge']" # TUNEAR
MARCAS_SESION_MUERTA  = ("/accounts/login/", "/challenge/")
```

Flujo de `capturar` por candidato:

1. `page.goto("https://www.instagram.com/")` + espera `networkidle`
2. Verificar sesión — si URL tiene marca de sesión muerta → `SesionInvalidaError`
3. Click `SELECTOR_SEARCH_ICON` → espera input visible
4. `page.keyboard.type(termino, delay=80)` → espera resultados del dropdown
5. Click `SELECTOR_TAB_POSTS` → espera `SELECTOR_POST_GRID`
6. `page.eval_on_selector_all(SELECTOR_POST_LINKS, ...)` → extrae hasta
   `posts_por_candidato × 2` hrefs (margen)
7. Si `termino in cfg_red["candidatos_posts"]`: abre cada link → captura pantalla
   completa con `browser.capturar_pagina(..., scrolls=1)`

Contexto de Gemini para cada post:
```
"publicación de Instagram sobre {termino}; la caption y los comentarios
visibles cuentan como publicaciones separadas"
```

### `config.json` — bloque nuevo bajo `"redes"`

```json
"instagram": {
  "scrolls_por_candidato": 1,   // scrolls sobre la grilla para extraer links; los posts se capturan con 1 scroll fijo
  "posts_por_candidato": 3,
  "candidatos_posts": [
    "Javier Milei",
    "Axel Kicillof",
    "Sergio Massa",
    "Patricia Bullrich",
    "Cristina Fernández de Kirchner"
  ]
}
```

### `accounts.py` — sin cambios de código

`cargar_pool("instagram")` ya funciona (lee `accounts-instagram.json`, que existe con
ig1/ig2 en estado `activa`). Sesiones en `.sesiones/instagram-<alias>.json`.
Login manual: `python accounts.py login ig1 --red instagram`.

### `run.py` / `run.ps1` — sin cambios de código

Solo actualizar la tarea de las 16:00 en Task Scheduler:
`-Redes twitter,tiktok,instagram`

## Flujo y cuota

**Corrida de las 10:00** (sin cambios): solo X — ~36 llamadas Gemini.

**Corrida de las 16:00** (actualizada):
- X: 12 candidatos × 3 capturas = ~36 llamadas
- TikTok: 12×3 búsqueda + 5×2 videos×2 capturas = ~56 llamadas
- Instagram: 5 punteros × 3 posts × 1 captura = **15 llamadas**
- Total 16:00: ~107 llamadas

**Día completo: ~143 llamadas** (12% más que el presupuesto con TikTok solo).
Dentro del rango con key secundaria y cascada de modelos. Si la cuota se agota,
aparece el warning "clasificación parcial" habitual — no rompe la corrida.

## Manejo de errores

Misma filosofía de degradación por partes:

| Error | Acción |
|---|---|
| Challenge / redirect a login | `SesionInvalidaError` → rotación de cuenta. Sin cuentas: bloque IG vacío, warning, corrida sigue |
| Falla abrir un post individual | Warning, continúa con el siguiente post |
| Grilla sin resultados | Warning, retorna `[]` para ese candidato |
| Total `< min_posts_electorales` o subida falla | No pisa snapshot, exit 1 (regla global) |

## Testing

Sin Playwright — toda la suite corre con mocks e HTML falso.

**Testdata:**
- `testdata/ig_grilla_falsa.html`: grid con thumbnails y links `href="/p/XXXXX/"` para
  testear extracción de links y dry-run de scroll+captura (mismo patrón que
  `feed_falso.html` de X)
- `testdata/ig_post_falso.html`: post individual con caption y comentarios

**`test_redes_instagram.py`:**
- `login_completado(url)` para URLs de login, challenge y home
- Extracción de links de post desde HTML falso (sin Playwright)
- Contexto de Gemini por tipo de captura
- Pool por red: `cargar_pool("instagram")` lee `accounts-instagram.json`
- `run.py` armando 3 bloques (twitter+tiktok+instagram) y payload fusionado correcto

## Límites honestos

- IG puede pedir verificación por SMS/email al loguear desde un dispositivo nuevo —
  el login manual lo maneja el usuario en la PC de la oficina
- El sidebar de IG cambia de DOM con frecuencia: `SELECTOR_SEARCH_ICON` y
  `SELECTOR_TAB_POSTS` tienen alta probabilidad de romperse cada tanto; se ajustan
  en la PC de la oficina con las cuentas reales (igual que hicimos con X y TikTok)
- Sin proxies, ambas cuentas comparten la IP de SMATA — si IG detecta patrones,
  puede challengear ambas a la vez; reponer cuentas es más simple que en TikTok
  (IG no suele pedir teléfono para cuentas nuevas)
