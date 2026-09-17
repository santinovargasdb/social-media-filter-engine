"""
Capa 3 — Consultoras automáticas (Boca de Urna).

Convierte una lista fija de consultoras en filas de comparación
{consultora, fecha, candidato, porcentaje, fuente_url, fuente_titulo}, extraídas
de notas periodísticas con Gemini y atadas SIEMPRE al link de la nota (fidelidad:
espejo fiel + fuente verificable).

No habla con SerpAPI ni Gemini "a mano": fetch vía fetcher.search_serpapi_web,
extracción vía gemini_client. `electoral` importa este módulo de forma diferida
para evitar el ciclo (este módulo importa canonical_key de electoral).
"""
import datetime
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import fetcher
import gemini_client

from electoral import canonical_key


# Lista fija EDITABLE de consultoras a seguir. Elegidas por prestigio metodológico
# y equilibrio de espectro (3 neutrales + 1 de cada polo). La "afiliación" es
# percepción pública debatida, ninguna se declara partidaria.
CONSULTORAS_DEFAULT = [
    "Opinaia",           # neutral / profesional
    "Poliarquía",        # neutral / establishment
    "Management & Fit",  # neutral / centro
    "CB Consultora",     # suele medir mejor al oficialismo/LLA
    "Zuban Córdoba",     # suele medir mejor al peronismo/kirchnerismo
]

# Descriptor de la elección para la query (editable, para no hardcodear el año).
ELECCION_LABEL = "presidencial 2027"

# Mapeo de ESPACIO/PARTIDO -> candidato principal (EDITABLE). En Argentina muchas
# encuestas reportan por espacio ("La Libertad Avanza 40%") en vez de por candidato.
# Con esto esos números se atribuyen al candidato que encabeza el espacio, para que
# crucen contra los candidatos detectados en redes. Claves en clave canónica
# (minúsculas, sin acentos). Editá los candidatos según la fórmula vigente.
ESPACIO_A_CANDIDATO = {
    # La Libertad Avanza / oficialismo
    "la libertad avanza": "Javier Milei",
    "lla": "Javier Milei",
    "oficialismo": "Javier Milei",
    "libertarios": "Javier Milei",
    # Peronismo / Unión por la Patria / kirchnerismo
    "union por la patria": "Axel Kicillof",
    "uxp": "Axel Kicillof",
    "peronismo": "Axel Kicillof",
    "peronismo kirchnerista": "Axel Kicillof",
    "peronismo k": "Axel Kicillof",
    "kirchnerismo": "Axel Kicillof",
    "fuerza patria": "Axel Kicillof",
    # Frente de Izquierda (FIT-U)
    "frente de izquierda": "Myriam Bregman",
    "fit": "Myriam Bregman",
    "fit-u": "Myriam Bregman",
    "izquierda": "Myriam Bregman",
    # Provincias Unidas / centro
    "provincias unidas": "Juan Schiaretti",
    "provincias unidos": "Juan Schiaretti",  # variante mal escrita frecuente
    # PRO / Juntos por el Cambio
    "juntos por el cambio": "Patricia Bullrich",
    "pro": "Mauricio Macri",
    "propuesta republicana": "Mauricio Macri",
    # UCR
    "ucr": "Facundo Manes",
    "union civica radical": "Facundo Manes",
    "radicalismo": "Facundo Manes",
}


def _map_espacio_a_candidato(nombre: str) -> str:
    """Si el nombre es un espacio/partido conocido, devuelve su candidato principal;
    si no, lo devuelve tal cual (se asume que ya es una persona)."""
    return ESPACIO_A_CANDIDATO.get(canonical_key(nombre), nombre)


def _parse_pct(raw) -> float:
    """'42,5' | '42.5' | 42 -> float. Lanza ValueError/TypeError si no es numérico."""
    return float(str(raw).strip().replace(",", "."))


def _parse_extraction(parsed: list, consultora: str, articles: list[dict]) -> list[dict]:
    """Mapea la respuesta de Gemini (espejo por 'Art_i') a filas de consultora con
    su fuente. Descarta ids inventados y porcentajes no numéricos. La fecha sale de
    la fila; si no vino, cae a la 'date' del artículo."""
    by_id = {f"Art_{i}": a for i, a in enumerate(articles)}
    rows: list[dict] = []
    for item in parsed or []:
        art = by_id.get(item.get("id", ""))
        if art is None:
            continue
        fecha = (item.get("fecha") or "").strip() or (art.get("date") or "").strip()
        for fila in item.get("filas", []) or []:
            candidato = (fila.get("candidato") or "").strip()
            if not candidato:
                continue
            candidato = _map_espacio_a_candidato(candidato)  # espacio/partido -> candidato
            try:
                pct = _parse_pct(fila.get("porcentaje"))
            except (TypeError, ValueError):
                continue
            rows.append({
                "consultora": consultora,
                "fecha": fecha,
                "candidato": candidato,
                "porcentaje": pct,
                "fuente_url": art.get("url", ""),
                "fuente_titulo": art.get("title", ""),
            })
    return rows


def _dedup_latest(rows: list[dict]) -> list[dict]:
    """Por (consultora, candidato canónico) conserva la fila de fecha más reciente."""
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r["consultora"].strip().lower(), canonical_key(r["candidato"]))
        cur = best.get(key)
        if cur is None or r.get("fecha", "") > cur.get("fecha", ""):
            best[key] = r
    return list(best.values())


# ── Config de fetch ───────────────────────────────────────────────────────────
ARTICULOS_POR_CONSULTORA = 2          # cuántas notas mirar por consultora
# Ventana de recencia: la búsqueda prioriza notas de los últimos N días (encuestas
# frescas). Si no hay nada reciente, cae a búsqueda amplia (mejor una nota vieja,
# claramente fechada, que ninguna). Editable.
RECENCY_DAYS = 120
# Consultoras en paralelo. Acotado a 3 (antes 5) para no gatillar el rate-limit del
# free-tier de Gemini: cada consultora hace 1 llamada, y el análisis social ya usa
# otras. 3 mantiene la latencia baja sin ráfagas de 5 llamadas simultáneas.
POLLSTER_FETCH_CONCURRENCY = 3
_ARTICLE_TIMEOUT = 15                 # seg por artículo
_ARTICLE_MAX_CHARS = 8000            # tope de texto que ve Gemini por nota

# ── Caché de TTL largo (las encuestas cambian lento) ──────────────────────────
_CACHE_TTL = 6 * 3600
_CACHE: dict[tuple, tuple[float, tuple[list, list]]] = {}

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _fetch_article_text(url: str) -> str:
    """Trae el texto legible de la nota (best-effort, sin dependencias). "" si falla
    (paywall/anti-bot/timeout); el caller cae al snippet de SerpAPI."""
    if not url:
        return ""
    try:
        resp = requests.get(url, timeout=_ARTICLE_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; SMATA-Monitor/1.0)"})
        resp.raise_for_status()
        html = resp.text
    except requests.exceptions.RequestException as e:
        print(f"DEBUG pollsters: no se pudo traer '{url}': {e}")
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:_ARTICLE_MAX_CHARS]


def _build_extract_prompt(consultora: str, articles: list[dict]) -> str:
    items = [{"id": f"Art_{i}", "titulo": a.get("title", ""), "texto": a.get("text", "")}
             for i, a in enumerate(articles)]
    items_text = json.dumps(items, ensure_ascii=False, indent=2)
    return f"""Sos un analista que extrae datos de encuestas electorales argentinas de notas periodísticas.

Vas a recibir una lista de notas en JSON, cada una con un "id" ("Art_0", "Art_1", ...), su "titulo" y su "texto". TODAS son sobre encuestas de la consultora "{consultora}".

Por cada nota, extraé ÚNICAMENTE los porcentajes de intención de voto presidencial que la consultora "{consultora}" reporta de forma EXPLÍCITA en el texto. Reglas:
1. Extraé solo números que estén literalmente en el texto. NO estimes ni inventes. Si la nota no trae porcentajes claros de esta consultora, devolvé "filas": [].
2. "candidato": el nombre de la persona candidata (COMPLETO y CANÓNICO, ej. "Milei" -> "Javier Milei"). Si la encuesta reporta por ESPACIO o PARTIDO en vez de por persona (ej. "La Libertad Avanza", "Unión por la Patria", "Frente de Izquierda", "Provincias Unidas"), devolvé el nombre del espacio/partido TAL CUAL (el sistema lo mapea al candidato que lo encabeza).
3. NO incluyas opciones que no son ni candidato ni espacio político: "voto en blanco", "en blanco", "impugnado", "indeciso", "no sabe / no contesta", "ninguno", "otros".
4. "fecha": fecha del sondeo en formato YYYY-MM-DD si aparece; si no, "".
5. Si la nota muestra VARIOS escenarios, preguntas o métricas, tomá SOLO la INTENCIÓN DE VOTO presidencial PRINCIPAL (primera vuelta, escenario general). IGNORÁ imagen/conocimiento, diferencial, aprobación de gestión, balotaje hipotético y sub-muestras (por provincia, edad, etc.). Asegurate además de que el número sea de la consultora "{consultora}" y no de otra consultora que la nota pueda citar.

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código) que sea un ESPEJO EXACTO de los ids recibidos, uno por nota. Formato exacto:
[
  {{ "id": "Art_0", "fecha": "2026-08-01", "filas": [ {{ "candidato": "Javier Milei", "porcentaje": 36.3 }} ] }}
]

Notas a procesar:
{items_text}"""


def _recency_tbs(days: int, today: datetime.date | None = None) -> str:
    """Arma el parámetro 'tbs' de SerpAPI para acotar Google a los últimos `days`
    días (formato de rango de fechas MM/DD/YYYY)."""
    today = today or datetime.date.today()
    cd_min = today - datetime.timedelta(days=days)
    return f"cdr:1,cd_min:{cd_min.strftime('%m/%d/%Y')},cd_max:{today.strftime('%m/%d/%Y')}"


def _fetch_one_consultora(consultora: str, country: str) -> tuple[list[dict], list[str]]:
    query = f'"{consultora}" encuesta intención de voto {ELECCION_LABEL}'
    # Priorizar notas recientes; si eso viene vacío O falla (None, p. ej. el tbs de
    # fechas rompe la búsqueda), caer a búsqueda amplia (mejor una nota vieja y
    # fechada que ninguna) antes de rendirse.
    resultados = fetcher.search_serpapi_web(
        query, max_results=ARTICULOS_POR_CONSULTORA, country=country,
        tbs=_recency_tbs(RECENCY_DAYS))
    if not resultados:
        resultados = fetcher.search_serpapi_web(
            query, max_results=ARTICULOS_POR_CONSULTORA, country=country)
    if resultados is None:
        return [], [f"{consultora}: el buscador no respondió."]
    if not resultados:
        return [], [f"No se encontraron encuestas recientes de {consultora}."]
    articles = []
    for r in resultados:
        # Unir SIEMPRE el snippet del buscador con el cuerpo del artículo: en diarios
        # con paywall/JS el cuerpo baja como cascarón (login/boilerplate) sin números,
        # y el snippet suele traer el titular con el porcentaje. El snippet va primero
        # para que sobreviva al tope de caracteres.
        snippet = r.get("snippet", "")
        cuerpo = _fetch_article_text(r.get("url", ""))
        text = ("\n\n".join(x for x in (snippet, cuerpo) if x))[:_ARTICLE_MAX_CHARS]
        articles.append({"url": r.get("url", ""), "title": r.get("title", ""),
                         "text": text, "date": r.get("date", "")})
    parsed, _status = gemini_client.run_with_rotation(_build_extract_prompt(consultora, articles))
    if parsed is None:
        return [], [f"{consultora}: no se pudo extraer (IA no disponible)."]
    rows = _parse_extraction(parsed, consultora, articles)
    if not rows:
        # Se encontraron notas pero no se extrajo ningún porcentaje: NO dejarlo en
        # silencio (tabla con columnas vacías sin aviso), explicar por qué.
        return [], [f"{consultora}: se encontraron notas pero no se pudo extraer "
                    f"ningún porcentaje (posible paywall o formato no reconocido)."]
    return rows, []


def fetch_pollster_rows(fecha_desde: str | None = None, country: str = "ar",
                        consultoras: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Trae filas de comparación de las consultoras seguidas, cada una con su fuente.
    Devuelve (rows, warnings). Cachea con TTL largo (las encuestas cambian lento)."""
    consultoras = consultoras or CONSULTORAS_DEFAULT
    cache_key = (tuple(consultoras), (country or "ar").strip().lower())
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL:
        print(f"DEBUG pollsters: cache HIT {cache_key}")
        return cached[1]

    def _one(consultora: str) -> tuple[list[dict], list[str]]:
        try:
            return _fetch_one_consultora(consultora, country)
        except Exception as e:
            print(f"ERROR pollsters[{consultora}]: {e}")
            return [], [f"{consultora}: error al procesar."]

    with ThreadPoolExecutor(max_workers=min(POLLSTER_FETCH_CONCURRENCY, len(consultoras))) as ex:
        resultados = list(ex.map(_one, consultoras))

    rows: list[dict] = []
    warnings: list[str] = []
    for crows, cwarn in resultados:
        rows.extend(crows)
        warnings.extend(cwarn)
    rows = _dedup_latest(rows)
    result = (rows, warnings)
    _CACHE[cache_key] = (now, result)
    return result
