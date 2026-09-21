# Boca de Urna — Scraper local por visión (Playwright + Gemini, en batch)

Fecha: 2026-09-21

## Contexto y decisiones cerradas

El cliente decidió: nada de APIs de scraping pagas; hacer el scraper propio. Como el
rate-limit lo pone X (no el tercero) y la máquina de la oficina es débil, la solución es:

- **Máquina**: PC de la oficina (Celeron G5905, 8 GB RAM, **sin GPU utilizable**). Mejorarla NO es opción.
- **Scraping = LOCAL**: Playwright corre en esa PC, con **cuentas descartables rotativas**, desde la **IP de SMATA**, **sin proxies**.
- **Lectura de capturas = Gemini** (free-tier). Como es **batch** (no en vivo), se pacea lento y entra en el límite gratis. La PC NO puede correr un modelo de visión local (muy débil).
- **Batch 2-3×/día** vía Task Scheduler de Windows. Si la PC está apagada, la app muestra la **última captura real**.
- **Integra** detrás del feature flag / arquitectura de bloques ya existente; la app pasa a **leer resultados pre-computados** de un almacén.

## Arquitectura

```
[PC oficina]  scraper_local (Python + Playwright)
   por red (X→IG/TikTok) y por candidato:
     login (cuenta rotativa) → buscar → scrollear → SCREENSHOT ──┐
                                                                  ▼
     Gemini visión (1 llamada por captura, paceada) ── extrae posts {texto,autor,fecha,red,candidato,postura}
                                                                  ▼
     dedup + agrega por candidato (reusa la lógica electoral existente)
                                                                  ▼
     escribe UN snapshot JSON (candidatos, por_red, evidencia, generado_en) ──► [ALMACÉN externo]
                                                                                        ▲
[Render backend]  la Boca de Urna, en modo "stored", LEE el último snapshot ───────────┘
                  y lo devuelve con la misma forma de siempre + meta.ultima_actualizacion
[Vercel frontend] muestra el resultado + "Última actualización: <fecha>"
```

## Componentes

### 1. `scraper_local/` (NUEVO — corre en la PC, NO se deploya a Render)
Módulos chicos y testeables por separado:
- **`browser.py`** — Playwright: abrir navegador, cargar sesión (cookies) de la cuenta activa, ir a la búsqueda de la red, scrollear N veces (hasta cubrir el plazo), sacar capturas. Selectores/flujo por red (X primero).
- **`accounts.py`** — cuentas descartables: pool en un archivo local, rotación, persistencia de cookies por cuenta, detección de "cuenta baneada/challenge" → pasar a la siguiente. Sin proxies.
- **`vision.py`** — manda cada captura a **Gemini visión** con un prompt que devuelve JSON: por cada post visible `{autor, fecha, texto, candidato(s), postura, confianza}`. **Una pasada** (extrae Y clasifica) para minimizar llamadas. Paceado bajo el RPM del free-tier.
- **`dedup.py`** — dedup entre capturas solapadas por `(autor + hash del texto)` o el permalink si es visible.
- **`run.py`** — orquesta: por red × candidato → browser → vision → dedup → agrega (reusa `electoral.aggregate_net_sentiment`/`_merge_bloques`) → arma el snapshot → lo sube al almacén. Es lo que dispara el Task Scheduler.

### 2. Almacén persistente (fuera de la oficina; Render lo lee)
Render free tiene disco efímero, así que el snapshot vive afuera. Recomendado: **Supabase** (Postgres free, REST simple) — el scraper hace `upsert` de una fila `snapshot` y el backend la lee. Alternativa más simple: **GitHub Gist** (el scraper actualiza un JSON vía token; Render lee el raw). Se elige uno; es swappable.

### 3. Backend (Render) — modo "stored"
- Nueva variable: `URNA_FETCH_BACKEND=stored` (se suma a `serpapi`/`scraper`). En ese modo, `run_boca_de_urna` **no busca en vivo**: lee el último snapshot del almacén y lo devuelve tal cual, con `meta.ultima_actualizacion`.
- Mismos tipos/forma que ya consume el frontend (candidatos con `por_red`, evidencia, etc.). Cero cambios de contrato.

### 4. Frontend
- Mostrar **"Última actualización: <fecha>"** (de `meta.ultima_actualizacion`) para que quede claro que es la última corrida del batch, no en vivo. El botón "Analizar" pasa a "Ver último análisis" (o refresca la lectura del snapshot).

## Cuota de Gemini (visión, en batch)
- 1 llamada por captura; se pacean bajo el RPM del free-tier. 2-3 corridas/día con decenas-cientos de capturas entran en el límite diario si se espacian. Si algún día roza el tope: se cortan capturas o se paga unos centavos de visión ese día. Como es batch, la lentitud/parcialidad no molesta (la app muestra el último snapshot igual).

## Testing
- **Se testea con mocks** (sin red ni browser): `vision.py` (parseo del JSON de Gemini a posts), `dedup.py`, el cliente del almacén, y el read-path del backend (modo stored devuelve el snapshot). La suite actual sigue verde.
- **La automatización del navegador se verifica a mano en la PC de la oficina** con cuentas reales — no se puede testear en CI (depende de login/selectors/anti-bot vivos). Se entrega con logs y capturas de debug para tunear allá.

## Límites conocidos (honestos)
- **Bans**: IP fija + sin proxies + cuentas descartables → X las va a ir baneando; la rotación + ritmo lento humano lo mitiga, pero se van a quemar cuentas (hay que reponerlas).
- **Fragilidad**: los selectors/flujo de login de X cambian; el scraper se va a romper cada tanto y hay que ajustarlo. La lectura por visión es más robusta a cambios de HTML, pero no al login/anti-bot.
- **Velocidad**: Playwright en un Celeron con 8 GB es lento; una corrida puede tardar bastante. Es batch, así que se tolera (corre de noche / 2-3×/día).
- **Tuning en la máquina**: los selectors, tiempos de espera y el flujo de login se ajustan sí o sí en la PC de la oficina; lo que entrego es la estructura + los parsers, no un scraper "llave en mano" que ande al primer intento.

## Plan de implementación (por fases)
1. **Andamiaje + almacén + read-path del backend** (modo `stored` + frontend "última actualización"), con un snapshot de ejemplo → la app muestra datos guardados. Testeable de punta a punta con mocks.
2. **`vision.py`** (captura → Gemini → posts) + tests de parseo con capturas de ejemplo.
3. **`scraper_local` para X**: browser + accounts + run, para X. Verificación a mano en la PC de la oficina.
4. **Agregar IG y TikTok**.
5. Tuning de volumen/pacing con datos reales.
