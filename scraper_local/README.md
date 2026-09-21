# Scraper local por visión (Boca de Urna)

Subsistema que corre **en la PC de la oficina** (no en Render): navega las redes con
Playwright, saca capturas, las lee con Gemini (visión) y sube un **snapshot** a Supabase.
El backend en Render, en modo `stored`, lee ese snapshot y la app lo muestra — así se ve
el último análisis real aunque la PC esté apagada.

Diseño completo: `docs/superpowers/specs/2026-09-21-scraper-local-vision-design.md`.

## Estado
- ✅ **Fase 1 (hecha):** almacén Supabase (`backend/store.py`) + modo `stored` en el
  backend + "Última actualización" en el frontend. Con un snapshot sembrado, la app ya
  muestra datos guardados.
- ⏳ Fase 2: `vision.py` (captura → Gemini → posts). Fase 3: el scraper de X (Playwright).

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
