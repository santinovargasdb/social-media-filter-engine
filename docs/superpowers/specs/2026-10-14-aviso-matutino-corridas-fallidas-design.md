# Aviso matutino de corridas fallidas

**Fecha:** 2026-10-14
**Componentes:** `scraper_local/` (nuevo `alertas.py`, cambios en `run.py`, nuevo
`aviso-matutino.ps1`, nuevo `test_alertas.py`) + una tarea nueva en Task Scheduler.

## Problema

El scraper corre dos veces por día (10:00 solo Twitter, 16:00 completa) en la PC
de la oficina. Cuando una corrida sale degradada o fallida —captcha que deja una
red en 0 (TikTok el 7/10), cuentas quemadas (5-8/10), subida fallida a Supabase—
no hay ningún aviso: el usuario se entera recién si abre los logs a mano o nota
que el tablero trae menos de lo habitual. La corrida pesada (16h) termina ~17:00,
cuando el usuario capaz ya no está frente a la PC.

**Objetivo:** que a las **9 de la mañana** —momento en que el usuario sí o sí está
frente a la PC— aparezca un cartel **a pantalla completa** que resuma cualquier
corrida con problemas ocurrida desde la última vez que lo miró. Si estuvo todo
bien, no aparece nada.

## Decisión de diseño (aprobada por el usuario)

Enfoque **híbrido**: Python **decide** (tiene los datos ricos de la corrida) y deja
el veredicto escrito en un archivo; PowerShell **muestra** el cartel nativo a las 9h.

### 1. Registro del resultado de cada corrida (`run.py` + `alertas.py`)

- Carpeta nueva `scraper_local/logs/resultados/`. Cada corrida escribe
  `logs/resultados/<runid>.json`, donde `<runid>` es el mismo sello
  `%Y%m%d-%H%M` (UTC) que ya usa el log y la carpeta de capturas.
- Contenido del JSON:

  ```json
  {
    "runid": "20261013-1903",
    "fin_utc": "2026-10-13T17:00:23+00:00",
    "redes": ["twitter", "tiktok", "instagram"],
    "exit_code": 0,
    "posts_por_red": { "twitter": 116, "tiktok": 319, "instagram": 47 },
    "warnings_total": 2,
    "cuentas_quemadas": [],
    "posts_electorales": 332,
    "nivel": "ok",
    "titulo": "",
    "mensaje": ""
  }
  ```

- `alertas.py` (lógica pura, sin I/O ni UI) expone
  `evaluar(resultado: dict, umbral_warnings: int = 8) -> dict | None`.
  Devuelve `{"nivel": "alerta", "titulo", "mensaje"}` si hay que avisar, o `None`
  si la corrida estuvo bien. **Regla** — es alerta si se cumple *cualquiera*:

  | # | Condición | Cómo se detecta |
  |---|-----------|-----------------|
  | 1 | No se pudo actualizar el tablero | `exit_code not in (0, 2)` |
  | 2 | Una red corrida trajo 0 posts | algún valor de `posts_por_red` == 0 |
  | 3 | Se quemó una cuenta | `cuentas_quemadas` no vacío |
  | 4 | Muchos avisos | `warnings_total >= umbral_warnings` (default 8) |

  `exit_code == 2` (ya había otra corrida / lock) **no** alerta: es normal al
  solaparse. El `mensaje` concatena un motivo por línea en lenguaje llano
  (p. ej. *"Sin posts en: tiktok (captcha / cuenta quemada). 19 avisos en la
  corrida."*). El `titulo` fijo: `⚠️ Boca de Urna: corrida con problemas`.

- `run.py` — cambios mínimos:
  - En `correr()`: acumular `posts_por_red` (ya se calcula `len(posts_red)` por
    red) y `cuentas_quemadas = [w for w in warnings if "marcada como quemada" in w]`.
  - Antes de cada `return` de `correr()`, armar el `resultado`, correr
    `alertas.evaluar(...)` para completar `nivel/titulo/mensaje`, y escribir el
    JSON con `_escribir_resultado(resultado)`.
  - En `main()`: envolver la llamada a `correr()` en `try/except`. Ante
    `InstanciaActivaError` → `exit_code=2`, `nivel="ok"` (no alerta). Ante
    cualquier otra excepción → escribir un resultado `exit_code=1`,
    `nivel="alerta"`, `mensaje="La corrida crasheó, revisá el log."`, y re-lanzar
    o devolver 1 (preservando el log del traceback).
  - `_limpiar_corridas_viejas` (o un helper nuevo) poda `logs/resultados/*.json`
    dejando las últimas ~60 (son archivitos chicos; evita acumulación infinita).

El umbral de avisos se lee de `config.json` → bloque nuevo opcional
`"alertas": { "umbral_warnings": 8 }`; si no está, usa 8.

### 2. Cartel matutino a pantalla completa (`aviso-matutino.ps1`)

Script nuevo que dispara la tarea de las 9h. Lógica:

1. Marcador `logs/resultados/.ultimo-visto` guarda el ISO-UTC de la última vez que
   el usuario cerró el cartel (si no existe, se asume epoch 0).
2. Leer todos los `logs/resultados/*.json`:
   - **Pendientes:** los que tienen `fin_utc > ultimo-visto` **y** `nivel == "alerta"`.
   - **Chequeo "no corrió":** si **ningún** resultado tiene `fin_utc` dentro de las
     últimas ~20 h, agregar el ítem *"No hubo ninguna corrida registrada en las
     últimas 20 h (¿PC apagada o la tarea no disparó?)."*
3. Si no hay pendientes ni "no corrió" → salir en silencio (no se muestra nada).
4. Si hay algo → mostrar **una** ventana a pantalla completa que liste cada
   corrida problemática (fecha/hora + su `mensaje`). Al cerrarla con el botón
   **"Entendido"** → escribir `ultimo-visto = ahora (UTC)`. Sólo se actualiza al
   cerrar: si el script se mata, la alerta reaparece la próxima.

**Ventana full-screen** (WinForms nativo, sin instalar nada):
`Add-Type -AssemblyName System.Windows.Forms,System.Drawing`; `Form` con
`FormBorderStyle='None'`, `WindowState='Maximized'`, `TopMost=$true`, fondo rojo
oscuro, título grande, el detalle en texto grande legible, y un botón "Entendido"
que cierra (también Escape). Se llama con `ShowDialog()` (bloquea sólo el proceso
del notificador, no el scraper). `Activate()` para traerla al frente.

El script acepta `-Simular` para forzar un cartel de ejemplo (verificación visual
sin esperar una falla real).

### 3. Tarea programada nueva

| Hora | Nombre de tarea | Comando |
|------|-----------------|---------|
| 09:00 | SMATA Boca de Urna - Aviso matutino (9h) | `powershell -NoProfile -File "<ruta>\scraper_local\aviso-matutino.ps1"` |

Debe ser **LogonType Interactive** (usuario logueado), igual que las otras tareas:
la ventana necesita un escritorio real. Se documenta en `scraper_local/README.md`
junto a la tabla de tareas existente.

## Componentes

| Archivo | Cambio |
|---|---|
| `scraper_local/alertas.py` | NUEVO — `evaluar(resultado, umbral_warnings=8)`, lógica pura |
| `scraper_local/run.py` | acumular `posts_por_red` + `cuentas_quemadas`; escribir `logs/resultados/<runid>.json` en cada salida de `correr()`; `try/except` en `main()`; poda de resultados viejos |
| `scraper_local/aviso-matutino.ps1` | NUEVO — lee resultados pendientes + chequeo "no corrió"; muestra ventana full-screen; marca "visto"; flag `-Simular` |
| `scraper_local/test_alertas.py` | NUEVO — tests de `evaluar()` con casos reales |
| `scraper_local/config.json` | bloque opcional `"alertas": { "umbral_warnings": 8 }` |
| `scraper_local/README.md` | fila nueva en la tabla de Task Scheduler + nota de la tarea de las 9h |

Sin cambios de backend ni de frontend. No agrega dependencias de pip (la ventana
es WinForms nativo; Python sólo escribe un JSON).

## Verificación

- **TDD en `alertas.py`:** `test_alertas.py` cubre — 13/10 sano (exit 0, redes >0,
  2 avisos) → `None`; 7/10 (tiktok=0) → alerta; 23 avisos → alerta; `exit_code=1`
  → alerta; `exit_code=2` (lock) → `None`; cuenta quemada → alerta; corrida
  matutina sólo-twitter con twitter>0 → `None` y con twitter=0 → alerta.
- `run.py`: correr `--dry-run` y confirmar que se escribe `logs/resultados/<runid>.json`
  con el `nivel` correcto; `python -m pytest` del scraper sigue verde.
- `aviso-matutino.ps1 -Simular`: ver la ventana full-screen (título, detalle,
  botón "Entendido", Escape) y que al cerrarla escribe `.ultimo-visto`.
- Prueba de punta a punta: forzar un resultado "alerta" a mano en
  `logs/resultados/`, borrar `.ultimo-visto`, correr el script sin `-Simular` y
  confirmar que aparece; correrlo de nuevo y confirmar que ya no aparece (quedó
  marcado como visto).
