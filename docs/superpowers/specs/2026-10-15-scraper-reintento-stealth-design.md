# Scraper más resistente a challenges: stealth + reintento in-run

**Fecha:** 2026-10-15
**Componentes:** `scraper_local/browser.py` (stealth en `pagina_con_sesion`) y
`scraper_local/run.py` (reintento + corte en `capturar_candidato`), con tests en
`test_browser.py` / `test_run.py`.

## Problema

El scraper pierde posts por challenges (captcha / feed que no carga). En los logs:
días con 1-4 challenges dispersos (lo común) y algún día de bloqueo total (7/10,
TikTok = 0). Dos huecos concretos en el código actual:

1. **Sin stealth.** `pagina_con_sesion` (browser.py) abre el contexto sin
   `user_agent`, `locale` ni `timezone_id`. En headless el UA delata
   `HeadlessChrome` y el navegador reporta UTC/inglés — señales de bot que suben
   la tasa de challenges.
2. **Ante challenge, saltea de una.** `capturar_candidato` (run.py) captura un
   `DesafioTemporalError` y hace `return []` sin reintentar. El caso transitorio
   más común —el timeout de 30s de Twitter con la sesión sana— se pierde aunque un
   segundo intento lo recuperaría.

**No se ataca la causa raíz** (IP fija de oficina) — eso es proxies, otro proyecto.
Acá: bajar la tasa de challenges (stealth) y recuperar los transitorios (reintento),
sin costo externo y sin romper lo que anda.

## Decisión de diseño (aprobada por el usuario)

Explícitamente **NO se rota de cuenta ante un challenge**: las dos cuentas salen por
la misma IP, chocarían el mismo muro (razonamiento ya presente en el código). El
reintento es con la **misma cuenta**.

### A. Stealth — `browser.py` `pagina_con_sesion`

- **User-agent derivado de la versión real del Chromium** que levanta Playwright,
  para que no se desactualice nunca. Helper puro nuevo:
  `_user_agent(version: str) -> str` — toma el major de `version` (ej. `"140.0.7259.5"`
  → `140`) y arma
  `"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"`.
  Si `version` no empieza con dígitos, usa un major de respaldo (`"120"`). Elimina el
  token `HeadlessChrome`.
- En `pagina_con_sesion`: tras `launch`, calcular `ua = _user_agent(navegador.version)`
  y pasar a `new_context(... user_agent=ua, locale="es-AR",
  timezone_id="America/Argentina/Buenos_Aires" ...)`. Se conservan `storage_state`,
  `viewport` y `permissions`.

Sin costo de tiempo; aplica a las tres redes.

### B. Reintento + corte por red — `run.py` `capturar_candidato`

Constantes nuevas en run.py: `ESPERA_REINTENTO = (20, 30)` (segundos, randomizado) y
`UMBRAL_CORTE_CHALLENGES = 3`.

`capturar_candidato` recibe un parámetro nuevo opcional `estado_red: dict | None = None`
(si es `None`, arranca con uno propio). `estado_red` lleva
`{"challenges_consecutivos": int}` compartido entre los candidatos de una misma red
dentro de la corrida. `correr` crea uno por red y lo pasa en cada llamada.

Comportamiento ante `DesafioTemporalError`:
- Si `challenges_consecutivos < UMBRAL_CORTE_CHALLENGES` **y** es el primer intento del
  candidato → log, `browser.esperar_aleatorio(ESPERA_REINTENTO)` y **reintenta la misma
  cuenta una vez**.
- Si ya reintentó, o el contador llegó al umbral (**corte por IP**: se asume bloqueo y
  se saltea rápido sin esperar) → incrementa `challenges_consecutivos`, agrega el
  warning de "salteado por challenge" (la cuenta sigue activa) y `return []`.
- Un **éxito** (primer intento o reintento) resetea `challenges_consecutivos = 0`.

Se mantiene intacto: rotación de cuenta ante `SesionInvalidaError` (quema), y el
`return []` ante `Exception` inesperada. El bucle externo de rotación de cuenta
(`range(2)`) no cambia; el reintento es un bucle interno sobre la **misma** cuenta.

Efecto en un día de bloqueo total (tipo 7/10): los primeros ~3 candidatos reintentan,
el contador llega al umbral y el resto se saltea rápido → suma pocos minutos, no 15-20.

### Parámetros

Quedan como **constantes** en el código (fáciles de tunear). No se agregan a
`config.json` (YAGNI); si en el futuro se quieren tunear sin editar código, se mueven
igual que `alertas.umbral_warnings`.

## Componentes

| Archivo | Cambio |
|---|---|
| `scraper_local/browser.py` | NUEVO helper puro `_user_agent(version)`; `pagina_con_sesion` pasa `user_agent`/`locale`/`timezone_id` al contexto |
| `scraper_local/run.py` | constantes `ESPERA_REINTENTO`, `UMBRAL_CORTE_CHALLENGES`; `capturar_candidato` con reintento + corte y parámetro `estado_red`; `correr` crea y pasa `estado_red` por red |
| `scraper_local/test_browser.py` | tests de `_user_agent` (major correcto, sin `HeadlessChrome`, fallback) |
| `scraper_local/test_run.py` | tests nuevos de reintento (challenge→éxito), persistencia (challenge×2→[]), corte (umbral→1 sola llamada); **actualizar** `test_capturar_candidato_challenge_no_quema_ni_rota` (ahora reintenta) y la lambda de `_preparar_correr` (nueva aridad con `estado_red`) |

Sin dependencias nuevas. Sin cambios de backend/frontend.

## Verificación

- `_user_agent`: unit test puro — `_user_agent("140.0.7259.5")` contiene
  `"Chrome/140.0.0.0"` y `"Windows NT 10.0"`, no contiene `"HeadlessChrome"`;
  `_user_agent("")` usa el major de respaldo sin romper.
- `capturar_candidato` (mockeando `twitter.capturar` y `browser.esperar_aleatorio`):
  - challenge en el 1er intento y éxito en el reintento → devuelve los posts,
    `twitter.capturar` llamado 2 veces, `challenges_consecutivos == 0`.
  - challenge en ambos intentos → `[]`, llamado 2 veces, `challenges_consecutivos == 1`,
    cuenta NO quemada.
  - `estado_red={"challenges_consecutivos": 3}` → sin reintento: `twitter.capturar`
    llamado 1 sola vez, `[]`.
  - `SesionInvalidaError` sigue quemando y rotando (test existente verde).
- Suite completa del scraper (`test_run.py test_browser.py test_alertas.py`) verde.
