# Scraper local por visión (Boca de Urna)

Subsistema que corre **en la PC de la oficina** (no en Render): navega las redes con
Playwright, saca capturas, las lee con Gemini (visión) y sube un **snapshot** a Supabase.
El backend en Render, en modo `stored`, lee ese snapshot y la app lo muestra — así se ve
el último análisis real aunque la PC esté apagada.

Diseño completo: `docs/superpowers/specs/2026-09-21-scraper-local-vision-design.md`.

## Estado
- ✅ **Fase 1:** almacén Supabase (`backend/store.py`) + modo `stored` + "Última actualización".
- ✅ **Fase 2:** `vision.py` (captura → Gemini visión → posts). Smoke test: `testdata/README.md`.
- ✅ **Fase 3:** el scraper de X (`accounts.py` + `browser.py` + `dedup.py` + `run.py`).
  **En producción desde 2026-09-23**: corre en la PC de la oficina vía Task
  Scheduler (tarea "SMATA Boca de Urna - Scraper", 10:00 y 16:00).
- ✅ **Fase 4a:** TikTok (`redes/tiktok.py`) — búsqueda + comentarios top de punteros.
- ⏳ **Fase 4b:** Instagram.

## 1) Crear la tabla en Supabase
En el proyecto de Supabase → **SQL Editor** → correr:

```sql
create table urna_snapshot (
  id bigint generated always as identity primary key,
  generado_en timestamptz not null default now(),
  payload jsonb not null
);

-- El backend (Render) lee con la anon key: habilitar lectura pública.
alter table urna_snapshot enable row level security;
create policy "lectura publica del snapshot"
  on urna_snapshot for select using (true);
-- (El scraper que escribe usa la SERVICE key, que saltea RLS — no hace falta policy de insert.)
```

## 2) Variables de entorno

**Backend (Render) — para LEER:**
```
URNA_FETCH_BACKEND=stored
SUPABASE_URL=https://<tu-proyecto>.supabase.co
SUPABASE_KEY=<anon key>        # Project Settings → API → anon/public
URNA_SNAPSHOT_TABLE=urna_snapshot   # opcional (es el default)
```

**Scraper local (la PC) — para ESCRIBIR (Fase 3):** igual pero con la **service_role** key
(Project Settings → API → service_role) en `SUPABASE_KEY`.

## 3) Sembrar un snapshot de ejemplo (para probar la Fase 1 ya)
En el **SQL Editor** de Supabase, insertá una fila de prueba:

```sql
insert into urna_snapshot (payload) values ('{
  "candidatos": [
    {"nombre":"Javier Milei","pct":46.3,"pos":10,"neg":17,"neu":10,"menciones":37,
     "pos_pct":27.0,"neg_pct":45.9,"neu_pct":27.0,"por_red":{"twitter":37}},
    {"nombre":"Victoria Villarruel","pct":17.5,"pos":10,"neg":3,"neu":1,"menciones":14,
     "pos_pct":71.4,"neg_pct":21.4,"neu_pct":7.1,"por_red":{"twitter":14}}
  ],
  "evidencia": [
    {"candidato":"Javier Milei","postura":"en_contra","cita":"ejemplo de cita de un posteo",
     "post":{"network":"twitter","author":"usuario","author_url":"https://x.com/usuario",
             "text":"ejemplo de cita de un posteo","post_url":"https://x.com/usuario/status/1","date":""}}
  ],
  "comparacion": [
    {"candidato":"Javier Milei","redes_pct":46.3,"consultoras":[],"promedio_consultoras":null,"gap_promedio":null}
  ],
  "meta": {"total_posts":120,"posts_electorales":67,"analizados":80,
           "disclaimer":"Termómetro de conversación en redes; no es una muestra representativa.",
           "warnings":[]}
}');
```

Con eso + las env vars del backend en `stored`, abrí la Boca de Urna: debería mostrar
esos datos y arriba **"🕒 Última actualización: …"**.

> El scraper real (Fase 3) va a generar este mismo JSON automáticamente y llamar a
> `store.write_snapshot(payload, generado_en_iso)` — la forma es idéntica.

## 4) Setup del scraper en la PC de la oficina (Fase 3)

Todo se corre DENTRO de `scraper_local\` (con el repo clonado):

```powershell
cd scraper_local
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

**Variables de entorno**: copiá `.env.example` como `.env` (queda git-ignored) y
completá los valores — `run.ps1` lo carga solo antes de cada corrida (sin `.env`
local cae al `backend\.env`; lo que ya venga seteado en el entorno tiene
prioridad). Si corrés `run.py` a mano sin `run.ps1`, cargá las variables antes.

> [!important] Cuál key de Supabase va acá
> La **SECRET key** (`sb_secret_...`): Project Settings → API Keys → pestaña
> "Secret keys" → Create/Reveal. En la UI nueva de Supabase "secret" es el nombre
> actual de la vieja **service_role** — el scraper ESCRIBE y necesita ese nivel.
> La "publishable" (`sb_publishable_...`, ex anon) NO sirve para escribir; esa es
> la de LECTURA que usa el backend en Render.

**Cuentas** (descartables, login manual una sola vez por cuenta):

```powershell
# X / Twitter
.venv\Scripts\python accounts.py login cuenta1            # se abre el navegador: logueá a mano; guarda solo al llegar al timeline
.venv\Scripts\python accounts.py estado                   # ver el pool de Twitter

# TikTok (Fase 4a)
.venv\Scripts\python accounts.py login tt1 --red tiktok   # igual: logueá a mano en la ventana
.venv\Scripts\python accounts.py estado --red tiktok      # ver el pool de TikTok
```

> [!warning] En esa ventana usá el login NATIVO de X (usuario + contraseña)
> NO uses "Continuar con Google": Google bloquea su login dentro de navegadores
> automatizados (pestaña en blanco / "este navegador no es seguro"). Si una cuenta
> fue creada con Google y no tiene contraseña, generásela con "¿Olvidaste tu
> contraseña?" desde un navegador normal. Las cuentas nuevas crealas siempre con
> mail + contraseña (registro nativo de X).

**Probar la maquinaria sin tocar X** (contra el feed falso):

```powershell
# en otra consola: python -m http.server 8123 --bind 127.0.0.1  (desde testdata\)
.venv\Scripts\python browser.py --url http://127.0.0.1:8123/feed_falso.html --scrolls 3
```

**Corrida completa** (primero en dry-run, que no sube nada):

```powershell
.venv\Scripts\python run.py --dry-run
.venv\Scripts\python run.py            # sube el snapshot a Supabase
```

**Config (`config.json`)** — formato `"redes": { "twitter": {...}, "tiktok": {...} }`.
Claves de TikTok:

| Clave | Descripción |
|-------|-------------|
| `videos_comentarios` | cuántos videos de cada puntero abrir para capturar comentarios |
| `scrolls_comentarios` | scrolls dentro del panel de comentarios por video |
| `candidatos_comentarios` | lista de candidatos cuyos videos también se comentan (punteros) |

> [!note] Cuota de visión TikTok
> TikTok solo corre en la tarea de las **16:00** (`-Redes twitter,tiktok`). Eso agrega
> ~56 llamadas extra a Gemini visión; el total del día queda ~128, bajo el techo medido
> el 2026-09-23 para el free-tier. Twitter + TikTok en la misma corrida de las 16:00.

**Task Scheduler** — dos tareas (con la PC prendida). Agregá `-ExecutionPolicy Bypass`
SOLO si la política de la máquina bloquea scripts locales (con RemoteSigned no hace falta):

| Hora | Nombre de tarea | Comando |
|------|-----------------|---------|
| 10:00 | SMATA Boca de Urna - Twitter | `powershell -NoProfile -File "<ruta>\scraper_local\run.ps1" -Redes twitter` |
| 16:00 | SMATA Boca de Urna - Twitter+TikTok | `powershell -NoProfile -File "<ruta>\scraper_local\run.ps1" -Redes twitter,tiktok` |

Config editable en `config.json` (scrolls, esperas, candidatos, mínimo de posts para subir).

**Qué tunear allá si algo no anda** (es lo esperable, X y TikTok cambian):

- **Twitter** (`redes/twitter.py`): `SELECTOR_FEED` (hoy `article`),
  `MARCAS_SESION_MUERTA`, `TIMEOUT_FEED_MS`.
- **TikTok** (`redes/tiktok.py`): `SELECTOR_RESULTADOS` (hoy `[data-e2e='search_top-item']`),
  `SELECTOR_LINKS_VIDEO` (hoy `a[href*='/video/']`), `SELECTOR_COMENTARIOS`,
  `SELECTOR_CAPTCHA`. Los selectores están marcados `# TUNEAR` en el fuente.
  Para probarlos sin correr la suite completa:
  ```powershell
  # Un candidato + dry-run: no navega real, imprime lo que haría
  .venv\Scripts\python run.py --dry-run --redes tiktok
  ```
  O contra el testdata local (sin autenticación):
  ```powershell
  # en otra consola: python -m http.server 8123 --bind 127.0.0.1  (desde testdata\)
  .venv\Scripts\python browser.py --url http://127.0.0.1:8123/busqueda_falsa_tiktok.html --scrolls 2
  ```
- `config.json`: esperas más largas si hay challenges; `headless: false` para VER
  qué pasa; menos candidatos para corridas más cortas.
- Cuentas quemadas: `accounts.py estado --red <red>` las muestra; reponer con
  `login <alias-nuevo> --red <red>`.
