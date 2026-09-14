"""
Capa 3 — Boca de Urna (Termómetro de Redes).

Reutiliza normalizer.fetch_posts (que a su vez usa fetcher/SerpAPI) para traer
publicaciones públicas, corre un análisis electoral con Gemini (candidato +
postura + confianza por post vía gemini_client), agrega en sentimiento neto por
candidato, y compara la brecha contra las consultoras cargadas por CSV.

No habla con SerpAPI ni con Gemini directamente: fetch vía normalizer, transporte
vía gemini_client. Stateless.
"""
import csv
import io
import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import gemini_client
import normalizer
from normalizer import UpstreamUnavailableError  # reexport para el endpoint

DISCLAIMER = (
    "Este indicador refleja el clima de conversación en redes sociales sobre "
    "publicaciones públicas indexadas. No es una muestra representativa del "
    "electorado ni una proyección de resultado electoral. Sirve como termómetro "
    "direccional, complementario a las encuestas de consultoras."
)

CONF_MIN = 0.5
POSTURAS_VALIDAS = ("a_favor", "en_contra", "neutro")
CSV_COLUMNS = ("consultora", "fecha", "candidato", "porcentaje")

# ── Lista fija de candidatos (EDITABLE) ───────────────────────────────────────
# EDITÁ ESTA LISTA con los candidatos de la elección vigente. La Boca de Urna hace
# UNA búsqueda dedicada por cada candidato (además de la búsqueda general) para
# garantizar que aparezcan VARIAS opiniones y no un solo candidato. Los nombres de
# acá también se cruzan contra los del CSV de consultoras.
CANDIDATOS_DEFAULT = [
    "Javier Milei",
    "Sergio Massa",
    "Patricia Bullrich",
    "Axel Kicillof",
    "Cristina Kirchner",
    "Mauricio Macri",
]

# Cuántos posts (como máximo) aporta cada término de búsqueda al corpus. Acota lo
# que trae la búsqueda general para dejar lugar a las búsquedas por candidato (si
# no, el término general coparía el corpus y volveríamos a ver un solo candidato).
POSTS_PER_TERM = 6
# Tope duro de posts que se mandan al clasificador electoral. Acotado para que el
# análisis entre cómodo bajo el timeout de 120s del frontend (más posts = más
# tokens y más lotes de Gemini = más lento).
ELECTORAL_MAX_POSTS = 36
# El clasificador electoral corre en lotes de este tamaño (una llamada a Gemini por
# lote) para no armar un prompt gigante y frágil con corpus grande.
ELECTORAL_BATCH_SIZE = 18

# ── Concurrencia (clave para no exceder el timeout de 120s del frontend) ──────
# Las búsquedas de SerpAPI y los lotes de Gemini son I/O bloqueante independiente.
# Antes corrían en SERIE (≈21 búsquedas + varios lotes uno atrás de otro) y el
# request tardaba >120s → timeout. Ahora corren en paralelo con pools acotados.
# SerpAPI: pool moderado para respetar el límite de concurrencia del plan.
FETCH_CONCURRENCY = 4
# Gemini: pool bajo para no gatillar los rate-limits del free-tier (run_with_rotation
# ya maneja 429/503, pero mejor no provocarlos con demasiadas llamadas simultáneas).
ELECTORAL_BATCH_CONCURRENCY = 2


def _parse_pct(raw: str) -> float:
    """Convierte '42,5' o '42.5' a float. Lanza ValueError si no es numérico."""
    return float(str(raw).strip().replace(",", "."))


def _valid_iso_date(raw: str) -> bool:
    import datetime
    try:
        datetime.date.fromisoformat(str(raw).strip())
        return True
    except ValueError:
        return False


def parse_pollster_csv(text: str) -> tuple[list[dict], list[str]]:
    """CSV de consultoras -> (filas_validas, warnings). Header inválido -> ValueError."""
    text = (text or "").strip()
    if not text:
        return [], []
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    faltantes = [c for c in CSV_COLUMNS if c not in header]
    if faltantes:
        raise ValueError(f"CSV inválido: faltan columnas {faltantes}. Se esperan {list(CSV_COLUMNS)}.")

    rows: list[dict] = []
    warnings: list[str] = []
    for i, raw in enumerate(reader, start=2):  # fila 1 = header
        consultora = (raw.get("consultora") or "").strip()
        fecha = (raw.get("fecha") or "").strip()
        candidato = (raw.get("candidato") or "").strip()
        if not consultora or not candidato:
            warnings.append(f"Fila {i}: consultora o candidato vacío — salteada.")
            continue
        if not _valid_iso_date(fecha):
            warnings.append(f"Fila {i}: fecha '{fecha}' no es YYYY-MM-DD — salteada.")
            continue
        try:
            pct = _parse_pct(raw.get("porcentaje", ""))
        except (TypeError, ValueError):
            warnings.append(f"Fila {i}: porcentaje '{raw.get('porcentaje')}' no numérico — salteada.")
            continue
        rows.append({"consultora": consultora, "fecha": fecha,
                     "candidato": candidato, "porcentaje": pct})
    return rows, warnings


def _build_electoral_prompt(items_para_prompt: list[dict]) -> str:
    items_text = json.dumps(items_para_prompt, ensure_ascii=False, indent=2)
    return f"""Sos un analista de opinión pública que evalúa publicaciones de redes sociales del ámbito argentino de cara a las próximas elecciones presidenciales.

Vas a recibir una lista de publicaciones en JSON. Cada una tiene un "id" único ("Post_0", "Post_1", ...), su "texto" y la "red".

REGLA DE AISLAMIENTO (OBLIGATORIA): Evaluá cada publicación de forma totalmente AISLADA e INDEPENDIENTE. Está PROHIBIDO que una publicación influya en el análisis de otra. Tratá cada "id" como un caso separado.

Por cada publicación determiná:
1. "es_electoral": true solo si la publicación habla de candidatos, partidos o la contienda electoral presidencial argentina; false si es ruido, spam u otro tema.
2. "candidatos": lista de los candidatos presidenciales mencionados. Por cada uno:
   - "nombre": el nombre COMPLETO y CANÓNICO del candidato (ej. si dice "Milei" o "el León", devolvé "Javier Milei"). Unificá alias y apodos al nombre canónico.
   - "postura": la postura del AUTOR del posteo hacia ese candidato. Exactamente uno de: "a_favor", "en_contra", "neutro".
   - "confianza": número entre 0 y 1 con tu certeza sobre esa postura.
   Si no hay candidatos, devolvé [].
3. "cita": el fragmento textual breve del posteo que justifica la postura (o "" si no aplica).

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código) que sea un ESPEJO EXACTO de los IDs recibidos: un objeto por publicación, con su mismo "id". No agregues ni omitas ninguno. Formato exacto:
[
  {{ "id": "Post_0", "es_electoral": true, "candidatos": [{{ "nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9 }}], "cita": "..." }}
]

Publicaciones a evaluar:
{items_text}"""


def canonical_key(nombre: str) -> str:
    """Clave de merge: sin acentos, minúsculas, espacios colapsados."""
    s = unicodedata.normalize("NFKD", (nombre or "").strip().lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.split())


def aggregate_net_sentiment(analysis: list[dict]) -> tuple[list[dict], int, bool]:
    """Agrega por candidato en sentimiento neto. Devuelve (candidatos, baja_confianza, fallback_volumen)."""
    acc: dict[str, dict] = {}
    baja_confianza = 0
    for item in analysis:
        for c in item.get("candidatos", []) or []:
            if c.get("confianza", 0) < CONF_MIN:
                baja_confianza += 1
                continue
            key = canonical_key(c["nombre"])
            entry = acc.setdefault(key, {"nombre": c["nombre"], "pos": 0, "neg": 0, "neu": 0, "menciones": 0})
            entry["menciones"] += 1
            if c["postura"] == "a_favor":
                entry["pos"] += 1
            elif c["postura"] == "en_contra":
                entry["neg"] += 1
            else:
                entry["neu"] += 1

    entries = list(acc.values())
    for e in entries:
        e["net"] = e["pos"] - e["neg"]
    suma_neto = sum(max(e["net"], 0) for e in entries)
    fallback_volumen = suma_neto <= 0
    suma_menciones = sum(e["menciones"] for e in entries)

    candidatos: list[dict] = []
    for e in entries:
        if fallback_volumen:
            pct = (e["menciones"] / suma_menciones * 100) if suma_menciones else 0.0
        else:
            pct = (max(e["net"], 0) / suma_neto * 100)
        candidatos.append({
            "nombre": e["nombre"], "pct": round(pct, 1),
            "pos": e["pos"], "neg": e["neg"], "neu": e["neu"], "menciones": e["menciones"],
        })
    candidatos.sort(key=lambda c: c["pct"], reverse=True)
    return candidatos, baja_confianza, fallback_volumen


def build_evidence(analysis: list[dict], posts_by_id: dict[str, dict], por_candidato: int = 5) -> list[dict]:
    """Empareja cada mención (conf >= CONF_MIN) con su post + cita, ordenada por
    confianza desc, capando a `por_candidato` citas por candidato."""
    _POST_FIELDS = ("network", "author", "author_url", "text", "post_url", "date")
    filas: list[tuple[float, dict]] = []
    for item in analysis:
        src = posts_by_id.get(item.get("id", ""))
        if src is None:
            continue
        post_subset = {k: src.get(k, "") for k in _POST_FIELDS}
        for c in item.get("candidatos", []) or []:
            if c.get("confianza", 0) < CONF_MIN:
                continue
            filas.append((c["confianza"], {
                "candidato": c["nombre"], "postura": c["postura"],
                "cita": item.get("cita", "") or (src.get("text", "") or ""),
                "post": post_subset,
            }))
    filas.sort(key=lambda t: t[0], reverse=True)
    vistos: dict[str, int] = {}
    evidencia: list[dict] = []
    for _conf, fila in filas:
        key = canonical_key(fila["candidato"])
        if vistos.get(key, 0) >= por_candidato:
            continue
        vistos[key] = vistos.get(key, 0) + 1
        evidencia.append(fila)
    return evidencia


def _keys_match(key_redes: str, key_csv: str) -> bool:
    """Considera que dos claves canónicas refieren al mismo candidato si una
    es subconjunto de palabras de la otra (permite alias de apellido solo).

    Nota de diseño: la AGREGACIÓN usa canonical_key estricto (igualdad exacta)
    para nunca fusionar personas distintas; la COMPARACIÓN usa este subset para
    reconciliar nombres tersos escritos a mano en el CSV contra nombres canónicos
    completos que devuelve Gemini. No "arreglar" esto a igualdad estricta.
    """
    # Guarda contra claves vacías: set("".split()) == set() es subconjunto de
    # cualquier conjunto, lo que haría que un nombre vacío matchee a todo el mundo.
    if not key_redes or not key_csv:
        return key_redes == key_csv
    if key_redes == key_csv:
        return True
    words_redes = set(key_redes.split())
    words_csv = set(key_csv.split())
    return words_csv.issubset(words_redes) or words_redes.issubset(words_csv)


def compare_vs_pollsters(candidatos: list[dict], pollster_rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Cruza los % de redes con los del CSV por candidato (última fecha por
    consultora). Devuelve (comparacion, warnings)."""
    # Última fecha por (candidato_key, consultora).
    latest: dict[tuple[str, str], dict] = {}
    for r in pollster_rows:
        k = (canonical_key(r["candidato"]), r["consultora"])
        if k not in latest or r["fecha"] > latest[k]["fecha"]:
            latest[k] = r
    # Agrupar por candidato_key -> {consultora: pct}.
    por_candidato: dict[str, dict[str, float]] = {}
    for (cand_key, consultora), r in latest.items():
        por_candidato.setdefault(cand_key, {})[consultora] = r["porcentaje"]

    comparacion: list[dict] = []
    matched_csv_keys: set[str] = set()
    for c in candidatos:
        key = canonical_key(c["nombre"])
        # Recopilar consultoras que hacen match (exacto o alias de palabras).
        # Si dos CSV keys distintas matchean el mismo candidato de redes y
        # ambas tienen datos de la MISMA consultora, promediamos (no pisamos)
        # para evitar perder un valor en colisiones de alias multi-match.
        consultoras_pct: dict[str, float] = {}
        for csv_key, por_cons in por_candidato.items():
            if _keys_match(key, csv_key):
                matched_csv_keys.add(csv_key)
                for consultora, pct in por_cons.items():
                    if consultora in consultoras_pct:
                        consultoras_pct[consultora] = round(
                            (consultoras_pct[consultora] + pct) / 2, 1
                        )
                    else:
                        consultoras_pct[consultora] = pct
        consultoras = [
            {"consultora": nombre, "pct": pct, "gap": round(c["pct"] - pct, 1)}
            for nombre, pct in sorted(consultoras_pct.items())
        ]
        if consultoras_pct:
            promedio = round(sum(consultoras_pct.values()) / len(consultoras_pct), 1)
            gap_promedio = round(c["pct"] - promedio, 1)
        else:
            promedio, gap_promedio = None, None
        comparacion.append({
            "candidato": c["nombre"], "redes_pct": c["pct"], "consultoras": consultoras,
            "promedio_consultoras": promedio, "gap_promedio": gap_promedio,
        })

    warnings: list[str] = []
    for cand_key, _consultoras_pct in por_candidato.items():
        if cand_key not in matched_csv_keys:
            nombre = next(r["candidato"] for r in pollster_rows if canonical_key(r["candidato"]) == cand_key)
            warnings.append(f"'{nombre}' aparece en el CSV de consultoras pero no se detectó en redes.")
    return comparacion, warnings


def build_candidate_search_list(pollster_rows: list[dict]) -> list[str]:
    """Candidatos a buscar: la lista fija editable (CANDIDATOS_DEFAULT) unida a los
    candidatos presentes en el CSV de consultoras (si se cargó), sin duplicar por
    clave canónica. La lista fija manda; el CSV solo suma nombres que no estaban."""
    nombres: list[str] = []
    vistos: set[str] = set()
    for nombre in list(CANDIDATOS_DEFAULT) + [r["candidato"] for r in pollster_rows]:
        nombre = (nombre or "").strip()
        key = canonical_key(nombre)
        if not key or key in vistos:
            continue
        vistos.add(key)
        nombres.append(nombre)
    return nombres


def run_boca_de_urna(keywords: list[str], networks: list[str], date: str | None,
                     country: str, pollster_csv: str) -> dict:
    """Orquesta la boca de urna. Arma el corpus con una búsqueda general MÁS una
    búsqueda dedicada por cada candidato (para captar varias opiniones), corre el
    análisis electoral y arma el payload. Lanza UpstreamUnavailableError
    (Gemini/SerpAPI caído) o ValueError (CSV con header inválido)."""
    # 1) CSV primero: si el header es inválido, cortamos con ValueError (-> 400).
    pollster_rows, csv_warnings = parse_pollster_csv(pollster_csv)
    warnings = list(csv_warnings)

    # 2) Corpus: búsqueda general + UNA por candidato (lista fija ∪ CSV), SIN el
    #    filtro de relevancia del monitor (fetch_raw_posts). El clasificador
    #    electoral decide qué es electoral. Esto es lo que garantiza que aparezcan
    #    varios candidatos y no uno solo. Cada término aporta hasta POSTS_PER_TERM
    #    posts para que la búsqueda general no cope el corpus.
    termino = " ".join([k for k in keywords if k and k.strip()]).strip() or "elecciones presidenciales"
    candidatos_buscar = build_candidate_search_list(pollster_rows)
    search_terms: list[str] = []
    vistos_terms: set[str] = set()
    for t in [termino] + [f"{c} {termino}".strip() for c in candidatos_buscar]:
        k = t.lower()
        if k and k not in vistos_terms:
            vistos_terms.add(k)
            search_terms.append(t)

    def _fetch(term: str) -> tuple[list[dict], bool]:
        try:
            return normalizer.fetch_raw_posts(
                termino=term, fecha_desde=date, keywords=keywords,
                accounts=[], networks=networks, country=country,
            )
        except Exception as e:  # una búsqueda que rompe no debe tumbar el análisis
            print(f"ERROR fetch_raw_posts term='{term}': {e}")
            return [], True

    # Búsquedas en PARALELO (antes en serie -> >120s). ex.map preserva el orden de
    # search_terms, así el merge es determinístico (general primero, luego candidatos).
    with ThreadPoolExecutor(max_workers=min(FETCH_CONCURRENCY, len(search_terms))) as ex:
        resultados_por_termino = list(ex.map(_fetch, search_terms))

    posts: list[dict] = []
    seen_urls: set[str] = set()
    any_upstream = False
    for raw, up in resultados_por_termino:
        any_upstream = any_upstream or up
        added = 0
        for p in raw:
            if added >= POSTS_PER_TERM:
                break
            url = p.get("post_url") or ""
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            posts.append(p)
            added += 1
    if len(posts) > ELECTORAL_MAX_POSTS:
        posts = posts[:ELECTORAL_MAX_POSTS]

    if not posts:
        # Distinguir "SerpAPI caído" (503) de "no hay resultados" (vacío legítimo).
        if any_upstream:
            raise UpstreamUnavailableError(
                "El buscador (SerpAPI) no respondió, probablemente por límite de "
                "cuota. Reintentá en un minuto.")
        warnings.append("No se encontraron publicaciones para el término buscado.")
        return {"candidatos": [], "evidencia": [], "comparacion": [],
                "meta": {"total_posts": 0, "posts_electorales": 0,
                         "disclaimer": DISCLAIMER, "warnings": warnings}}

    # 3) Análisis electoral (ids estables Post_i).
    posts_by_id = {f"Post_{i}": p for i, p in enumerate(posts)}
    analysis = analyze_posts_electoral(posts_by_id)
    if analysis is None:
        raise UpstreamUnavailableError(
            "Gemini no está disponible (cuota agotada o servicio caído). Reintentá en unos minutos.")

    posts_electorales = sum(1 for a in analysis if a.get("es_electoral") and a.get("candidatos"))

    # 4) Agregación + evidencia + comparación.
    candidatos, baja_conf, fallback_vol = aggregate_net_sentiment(analysis)
    evidencia = build_evidence(analysis, posts_by_id)
    comparacion, comp_warnings = compare_vs_pollsters(candidatos, pollster_rows)
    warnings.extend(comp_warnings)

    if not candidatos:
        warnings.append("No se detectaron candidatos en las publicaciones analizadas.")
    if fallback_vol and candidatos:
        warnings.append("Sentimiento neto no discriminó (todos ≤ 0): el % se calculó por volumen de menciones.")
    if baja_conf:
        warnings.append(f"{baja_conf} mención(es) descartada(s) por baja confianza (< {CONF_MIN}).")

    return {
        "candidatos": candidatos, "evidencia": evidencia, "comparacion": comparacion,
        "meta": {"total_posts": len(posts), "posts_electorales": posts_electorales,
                 "disclaimer": DISCLAIMER, "warnings": warnings},
    }


def analyze_posts_electoral(posts_by_id: dict[str, dict]) -> list[dict] | None:
    """Clasifica cada post (candidato + postura + confianza) vía Gemini, en LOTES de
    ELECTORAL_BATCH_SIZE (una llamada por lote) para no armar un prompt gigante y
    frágil con corpus grande. Devuelve el mirror saneado por id, o None SOLO si
    TODOS los lotes fallaron por upstream; si al menos uno anduvo, devuelve los
    resultados parciales (mejor eso que un 503 total)."""
    if not posts_by_id:
        return []
    items = list(posts_by_id.items())
    batches = [dict(items[i:i + ELECTORAL_BATCH_SIZE])
               for i in range(0, len(items), ELECTORAL_BATCH_SIZE)]

    # Lotes en PARALELO (antes en serie -> sumaba el tiempo de cada llamada a Gemini).
    # Pool bajo (ELECTORAL_BATCH_CONCURRENCY) para no gatillar rate-limits del free-tier.
    out: list[dict] = []
    alguno_ok = False
    with ThreadPoolExecutor(max_workers=min(ELECTORAL_BATCH_CONCURRENCY, len(batches))) as ex:
        for res in ex.map(_analyze_electoral_batch, batches):
            if res is None:
                print("DEBUG electoral: un lote falló (upstream).")
                continue
            alguno_ok = True
            out.extend(res)
    if not alguno_ok:
        return None
    return out


def _analyze_electoral_batch(posts_by_id: dict[str, dict]) -> list[dict] | None:
    """Corre UN lote por Gemini. Devuelve el mirror saneado por id, o None si el
    transporte falló (upstream)."""
    if not posts_by_id:
        return []
    items_para_prompt = [
        {"id": pid, "red": src.get("network", ""), "texto": src.get("text", "") or ""}
        for pid, src in posts_by_id.items()
    ]
    prompt = _build_electoral_prompt(items_para_prompt)
    parsed, _status = gemini_client.run_with_rotation(prompt)
    if parsed is None:
        return None

    out: list[dict] = []
    for item in parsed:
        pid = item.get("id", "") or ""
        if pid not in posts_by_id:
            continue  # id inventado o repetido por la IA
        candidatos_saneados = []
        for c in item.get("candidatos", []) or []:
            nombre = (c.get("nombre") or "").strip()
            postura = (c.get("postura") or "").strip().lower()
            if not nombre or not canonical_key(nombre) or postura not in POSTURAS_VALIDAS:
                continue
            try:
                conf = float(c.get("confianza", 0))
            except (TypeError, ValueError):
                conf = 0.0
            candidatos_saneados.append({"nombre": nombre, "postura": postura, "confianza": conf})
        out.append({
            "id": pid,
            "candidatos": candidatos_saneados,
            "cita": (item.get("cita") or "").strip(),
            "es_electoral": bool(item.get("es_electoral", False)),
        })
    return out
