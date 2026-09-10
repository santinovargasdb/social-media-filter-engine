# Monitor de Medios SMATA

Monitor institucional de medios y redes sociales para el Departamento de Prensa de SMATA (sindicato automotriz argentino). Busca publicaciones en X/Twitter, Instagram y TikTok, las puntúa por relevancia con IA y arma informes en Word.

## Stack técnico

| Capa | Tecnología |
|------|------------|
| Frontend | Next.js 14 (App Router) → **Vercel** |
| Backend | FastAPI (Python) → **Render** (`uvicorn`, ver `Procfile`) |
| Búsqueda | SerpAPI (Google con `site:` por red) |
| Scoring IA | Google Gemini 2.5 Flash (cascada de modelos) |
| Informes | `python-docx` |
| Secrets | Variables de entorno en Render (backend) y Vercel (frontend) |

## Arquitectura del backend (3 capas)

El backend respeta una separación estricta por capas; cada cambio se hace de forma quirúrgica sobre la capa que corresponde:

- **Capa 1 — Controlador/API** (`main.py`): endpoints, CORS, modelos Pydantic.
- **Capa 2 — Fetcher ciego** (`fetcher.py`): solo SerpAPI. No conoce a las otras capas.
- **Capa 3 — Normalización/IA** (`normalizer.py`): scoring con Gemini, parseo de URLs, filtros, y la orquestación `fetch_posts`.

Dependencia: `main.py → normalizer.py → fetcher.py`.

La **Boca de Urna** extiende la Capa 3 con un módulo aislado (`electoral.py`) que reutiliza `fetch_posts` de `normalizer.py` y el transporte Gemini de `gemini_client.py`, sin tocar las otras capas. Dependencia adicional: `main.py → electoral.py → gemini_client.py` y `electoral.py → normalizer.py`.

## Boca de Urna (Termómetro de Redes)

Página separada (`/boca-de-urna`) que analiza el **clima de conversación electoral** en redes sociales.

**Qué hace:** busca publicaciones sobre candidatos presidenciales, las clasifica por postura (a favor / en contra / neutro) mediante Gemini, agrega el sentimiento neto por candidato, y contrasta los porcentajes de redes con encuestas de consultoras cargadas vía CSV.

**Encuadre obligatorio:** no es una encuesta ni una proyección de resultado. Es un termómetro direccional sobre publicaciones públicas indexadas, no una muestra representativa del electorado. El disclaimer se muestra siempre en la interfaz (`DisclaimerBanner`) y se incluye en cada respuesta del endpoint (`meta.disclaimer`).

**Cómo funciona (flujo):**

1. El usuario ingresa keywords electorales, selecciona redes y (opcionalmente) carga un CSV de encuestas.
2. `electoral.py` llama a `normalizer.fetch_posts` con `smata_mode=False` para obtener el corpus.
3. Gemini clasifica cada post: candidato mencionado + postura + confianza (umbral `CONF_MIN = 0.5`).
4. `aggregate_net_sentiment` agrega `pos - neg` por candidato y calcula el % de sentimiento neto.
5. `compare_vs_pollsters` cruza esos % con las encuestas del CSV (última fecha por consultora).
6. La respuesta incluye `candidatos`, `evidencia` (citas + post), `comparacion` y `meta` (disclaimer + warnings).

**Módulos clave:**

| Archivo | Responsabilidad |
|---------|----------------|
| `backend/gemini_client.py` | Transporte HTTP compartido a Gemini: cascada de modelos + rotación de API Key por cuota |
| `backend/electoral.py` | Lógica electoral: parse del CSV, análisis con Gemini, agregación, comparación vs consultoras |
| `frontend/app/boca-de-urna/page.tsx` | Página Next.js 14 con layout de dos columnas (sentimiento + evidencia / comparación) |

## Estructura

```text
/
├── vercel.json                  ← Build del frontend en Vercel
├── .github/workflows/
│   └── keep-warm.yml            ← Ping periódico al backend (anti cold-start)
│
├── frontend/                    ← Next.js 14 (App Router) → Vercel
│   ├── app/
│   │   ├── page.tsx             ← Pantalla principal del monitor
│   │   ├── layout.tsx           ← Layout global, header y toggle de tema
│   │   ├── globals.css          ← Variables CSS (tema claro/oscuro + branding)
│   │   └── boca-de-urna/
│   │       └── page.tsx         ← Termómetro de Redes (Boca de Urna)
│   ├── components/
│   │   ├── SearchPanel.tsx      ← Panel de parámetros de búsqueda
│   │   ├── PostCard.tsx         ← Card de un post analizado
│   │   ├── ResultsGrid.tsx      ← Grilla de resultados rankeados
│   │   ├── ReportButton.tsx     ← Botón de informe Word
│   │   ├── ScoreBadge.tsx       ← Badge de score
│   │   ├── ThemeToggle.tsx      ← Toggle de tema claro/oscuro
│   │   └── urna/
│   │       ├── UrnaParamsBar.tsx    ← Panel de parámetros (keywords, CSV, redes, fecha)
│   │       ├── DisclaimerBanner.tsx ← Aviso de encuadre (no encuesta)
│   │       ├── SentimentBarChart.tsx← Barras de sentimiento neto por candidato
│   │       ├── EvidencePanel.tsx    ← Citas + posts con postura
│   │       └── ComparisonTable.tsx  ← Tabla redes vs consultoras (gap)
│   └── lib/
│       ├── api.ts               ← Cliente HTTP del backend (monitor de medios)
│       └── urnaApi.ts           ← Cliente HTTP del backend (Boca de Urna)
│
└── backend/                     ← FastAPI → Render
    ├── main.py                  ← Capa 1: Controlador/API
    ├── fetcher.py               ← Capa 2: Fetcher ciego (SerpAPI)
    ├── normalizer.py            ← Capa 3: Normalización/Filtro/IA (Gemini)
    ├── gemini_client.py         ← Transporte Gemini compartido (cascada + rotación)
    ├── electoral.py             ← Capa 3: Lógica electoral (Boca de Urna)
    ├── docx_generator.py        ← Generación del informe Word
    ├── config.py                ← Colores institucionales del informe
    ├── test_fetcher.py          ← Tests de la Capa 2 (pytest)
    ├── test_normalizer.py       ← Tests de la Capa 3 (pytest)
    ├── test_gemini_client.py    ← Tests del transporte Gemini (pytest)
    ├── test_electoral.py        ← Tests de la lógica electoral (pytest)
    ├── test_main_urna.py        ← Tests del endpoint /api/boca-de-urna (pytest)
    ├── Procfile                 ← Comando de arranque en Render
    ├── requirements.txt
    └── requirements-dev.txt     ← Dependencias de desarrollo (pytest)
```

## Setup local

### Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Windows  (source venv/bin/activate en Linux/Mac)
pip install -r requirements.txt

# Crear backend/.env con las claves:
#   SERPAPI_API_KEY=tu_clave
#   GEMINI_API_KEY=tu_clave

python main.py                   # o: uvicorn main:app --reload
# → http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install

# Crear frontend/.env.local con:
#   NEXT_PUBLIC_API_URL=http://localhost:8000

npm run dev
# → http://localhost:3000
```

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
```

## Deploy

### Frontend → Vercel

1. Conectar el repositorio en [vercel.com](https://vercel.com).
2. Dejar el **Root Directory** vacío (raíz del proyecto): Vercel usa `vercel.json` y buildea `frontend/`.
3. En **Environment Variables**, agregar `NEXT_PUBLIC_API_URL` = URL pública del backend en Render (ej. `https://tu-backend.onrender.com`). Es una variable de build: requiere redeploy al cambiarla.

### Backend → Render

1. Crear un **Web Service** apuntando a `backend/` (Render usa el `Procfile`: `uvicorn main:app`).
2. En **Environment**, agregar:
   - `SERPAPI_API_KEY`
   - `GEMINI_API_KEY`
   - `ALLOWED_ORIGINS` (opcional; orígenes permitidos por CORS, separados por coma).

> Nota: en el plan free de Render la instancia se duerme tras ~15 min de inactividad y la primera request luego tarda ~40-50 s. El workflow `keep-warm.yml` mitiga esos cold-starts.

## API

### `POST /api/search`

Envía los criterios de búsqueda y retorna los posts parseados y puntuados por la IA.

```json
{
  "keywords": ["SMATA", "paritaria"],
  "hashtags": ["smata"],
  "accounts": [],
  "networks": ["twitter", "instagram", "tiktok"],
  "date": null,
  "smata_mode": false
}
```

`smata_mode`: `true` = filtro estricto (solo SMATA / sector automotor), `false` = monitor de prensa amplio.

### `POST /api/generate-docx`

Recibe los posts seleccionados y devuelve el informe en formato Word (`.docx`).

### `POST /api/boca-de-urna`

**Termómetro de Redes** — analiza el sentimiento electoral en publicaciones públicas de redes sociales y lo contrasta con encuestas de consultoras.

> **Encuadre:** Este endpoint **no es una encuesta ni una proyección electoral**. Refleja el clima de conversación en publicaciones públicas indexadas (una muestra no representativa del electorado). Sirve como termómetro direccional, complementario a las consultoras.

**Request:**

```json
{
  "keywords": ["Milei", "Cristina", "elecciones presidenciales"],
  "networks": ["twitter", "instagram"],
  "date": "2025-09-01",
  "country": "ar",
  "pollster_csv": "consultora,fecha,candidato,porcentaje\nConsultora X,2025-09-01,Javier Milei,35.5\nConsultora X,2025-09-01,Cristina Kirchner,28.0"
}
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `keywords` | `string[]` | Términos de búsqueda (candidatos, partidos, etc.) |
| `networks` | `string[]` | Redes a consultar: `"twitter"`, `"instagram"`, `"tiktok"` |
| `date` | `string \| null` | Fecha mínima de publicación (`YYYY-MM-DD`) o `null` |
| `country` | `string` | Código ISO 3166-1 alpha-2 (ej. `"ar"`). Default: `"ar"` |
| `pollster_csv` | `string` | Contenido del CSV de encuestas (puede estar vacío) |

**Response (HTTP 200):**

```json
{
  "candidatos": [
    { "nombre": "Javier Milei", "pct": 55.0, "pos": 11, "neg": 3, "neu": 2, "menciones": 16 },
    { "nombre": "Cristina Kirchner", "pct": 45.0, "pos": 7, "neg": 4, "neu": 1, "menciones": 12 }
  ],
  "evidencia": [
    {
      "candidato": "Javier Milei",
      "postura": "a_favor",
      "cita": "El presidente está cambiando el país",
      "post": {
        "network": "twitter",
        "author": "usuario123",
        "author_url": "https://twitter.com/usuario123",
        "text": "El presidente está cambiando el país #Milei",
        "post_url": "https://twitter.com/usuario123/status/123",
        "date": "2025-09-03"
      }
    }
  ],
  "comparacion": [
    {
      "candidato": "Javier Milei",
      "redes_pct": 55.0,
      "consultoras": [
        { "consultora": "Consultora X", "pct": 35.5, "gap": 19.5 }
      ],
      "promedio_consultoras": 35.5,
      "gap_promedio": 19.5
    }
  ],
  "meta": {
    "total_posts": 50,
    "posts_electorales": 28,
    "disclaimer": "Este indicador refleja el clima de conversación en redes sociales...",
    "warnings": []
  }
}
```

**Errores:**

| HTTP | Causa |
|------|-------|
| `400` | CSV de consultoras con header inválido o dato malformado irrecuperable |
| `500` | error interno inesperado |
| `503` | Gemini o SerpAPI sin cuota o caídos |

**CSV de consultoras — esquema:**

El campo `pollster_csv` acepta el contenido (como string) de un CSV con las siguientes columnas obligatorias:

| Columna | Formato | Ejemplo |
|---------|---------|---------|
| `consultora` | texto | `Consultora X` |
| `fecha` | `YYYY-MM-DD` | `2025-09-01` |
| `candidato` | texto (nombre canónico) | `Javier Milei` |
| `porcentaje` | número (`42.5` o `42,5`) | `35.5` |

- Las filas con `consultora` o `candidato` vacíos, fecha no ISO-8601 o porcentaje no numérico se **saltean** con un warning (no abortan la operación).
- Si el **header** tiene columnas faltantes, el endpoint responde `400` de inmediato.
- Si se carga más de una fila por `(consultora, candidato)`, se usa la de **fecha más reciente**.
- El CSV puede estar vacío (`""`): en ese caso se omite la comparación vs consultoras.

### Modelo de datos (`Post`)

```ts
interface Post {
  id: string;
  network: "twitter" | "instagram" | "tiktok";
  author: string;
  author_url: string;
  text: string;
  date: string;
  post_url: string;
  relevance_score: number;          // 0-100, calculado por Gemini
  relevance_level: "alta" | "media" | "baja";
  matched_terms: string[];
  video_url: string | null;
}
```

## Branding SMATA

Colores institucionales aplicados en la interfaz:

```css
--smata-green-dark:  #1B4D2E;
--smata-green-mid:   #2E7D32;
--smata-green-light: #4CAF50;
--smata-green-pale:  #E8F5E9;
--smata-gold:        #FFC107;
```
