# Boca de Urna — Fase 2: `vision.py` (captura → Gemini visión → posts)

Fecha: 2026-09-22
Diseño padre: `2026-09-21-scraper-local-vision-design.md` (aprobado; Fase 1 hecha).

## Alcance

Implementar el lector de capturas del scraper local: dada una captura de pantalla de
una red social, extraer y clasificar en UNA pasada los posts visibles, con la misma
semántica direccional que el clasificador de texto existente. Sin navegador, sin
`dedup.py`, sin `run.py` (eso es Fase 3).

## Componentes

### 1. Transporte de visión en `backend/gemini_client.py` (extensión, no duplicación)

`gemini_client.py` es el transporte compartido de Gemini (cascada de 4 modelos +
rotación de API key), pero hoy solo arma payloads de texto. Se agrega:

- `generate_vision_raw(model, api_key, prompt, image_b64, mime, timeout)` — igual a
  `generate_raw` pero el payload lleva `parts: [{text}, {inline_data: {mime_type, data}}]`.
- La cascada y la rotación se REUTILIZAN (refactor mínimo: las funciones de cascada
  aceptan el generador de payload o los bytes de imagen como parámetro opcional).
  Los 4 modelos de la cascada (`gemini-2.5-flash` → `2.0-flash-lite`) son todos
  multimodales, así que la cascada sirve tal cual.
- Cero cambios de comportamiento para los llamadores de texto existentes
  (normalizer / electoral): la suite actual sigue verde.

### 2. `scraper_local/vision.py` — el lector de capturas

- **API pública**: `read_capture(image, red, candidatos=None) -> list[dict]`.
  `image` acepta ruta o bytes; `red` es la red de la captura (`twitter` primero);
  `candidatos` opcional para dar contexto al prompt (default: los del backend).
- **Prompt de visión**: hereda las reglas del prompt electoral de `electoral.py`
  (nombre canónico, postura direccional `a_favor`/`en_contra`/`neutro`, REGLA DE
  ENCUESTAS balanceada, cita textual) y agrega las de lectura de pantalla:
  - Extraer SOLO posts completamente visibles; los cortados por el borde se ignoran.
  - Ignorar UI de la red (menús, tendencias, "a quién seguir", publicidad marcada).
  - No inventar nada: autor = handle visible; fecha tal como se ve (relativa "2h" ok,
    string tal cual); si un dato no se ve, va `""`.
  - Devolver SOLO JSON (lista de posts).
- **Forma de salida** (saneada, agregable por la lógica electoral en Fase 3):
  ```json
  {"texto": "...", "autor": "@handle", "fecha": "2h", "red": "twitter",
   "es_electoral": true,
   "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}],
   "cita": "..."}
  ```
- **Saneo** (mismo espíritu que `electoral._sanitize`): postura validada contra
  `POSTURAS_VALIDAS`, confianza clampeada a [0,1], posts sin `texto` descartados,
  candidatos sin nombre descartados, respuesta no-lista → `[]` con warning.
- **Paceo free-tier**: `VISION_MIN_INTERVAL` (segundos, default 10) — `read_capture`
  espera lo que falte del intervalo desde la última llamada (reloj monótono). Con
  `VISION_MIN_INTERVAL=0` no espera (para tests y para el día que haya API paga).
- **CLI**: `python vision.py captura.png --red twitter [--candidatos "A,B"]` imprime
  el JSON extraído. Es la herramienta de tuning para la PC de la oficina.
- **Import del backend**: `vision.py` inserta `../backend` en `sys.path` (mismo
  patrón que usará `run.py` en Fase 3 para reusar `electoral`).

### 3. Tests

- **Unit tests con mocks** (`scraper_local/test_vision.py` + ampliación de
  `backend/test_gemini_client.py`), sin red:
  - transporte visión: payload con `inline_data`, cascada ante 429, rotación de key;
  - parseo: respuesta bien formada, JSON inválido, no-lista, posts sin texto,
    posturas inválidas, confianza fuera de rango;
  - paceo: respeta el intervalo (reloj mockeado), `0` no espera;
  - CLI: imprime JSON válido con Gemini mockeado.
  - Corren con la misma suite: `backend/.venv/Scripts/python.exe -m pytest -q`
    (backend) + `-q scraper_local` (nuevos).
- **Smoke test real (manual, una vez)**: `scraper_local/testdata/feed_falso.html` —
  un feed estilo X con 5 posts inventados de contenido conocido (elogio, crítica,
  encuesta con porcentajes, mención neutra, ruido no electoral). Se screenshotea con
  Playwright local, se pasa por `vision.py` con la API key real y se verifica que
  extraiga los 4 posts electorales con las posturas esperadas y descarte el ruido.
  El HTML y la captura quedan como fixture para re-probar el prompt cuando cambie.

## Fuera de alcance (Fase 3+)

`dedup.py`, `browser.py`, `accounts.py`, `run.py`, la subida del snapshot a Supabase
y toda la automatización del navegador en la PC de la oficina.

## Restricciones heredadas

- Sin dependencias nuevas en backend ni scraper (stdlib + `requests`; Playwright solo
  se usa a mano para el smoke test, no es dependencia del código).
- El encuadre institucional no cambia (esta fase no toca frontend ni textos).
