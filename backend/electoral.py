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
#
# OJO CON LA CUOTA: cada nombre = 1 búsqueda extra de SerpAPI por análisis. Con
# esta lista (12) son ~15 búsquedas por análisis. Si tu plan de SerpAPI es chico,
# recortá a los 5-6 nombres núcleo. Para 2027 las fórmulas aún no están cerradas;
# esto son los contendientes/referentes presidenciales más firmes por espacio.
# Otros nombres mencionados que podés sumar si querés: Victoria Villarruel ya está;
# Jorge Macri, Ignacio Torres, Rogelio Frigerio, Maximiliano Pullaro, Gustavo
# Valdés, Rodrigo de Loredo, Martín Llaryora, Ricardo Quintela, Nicolás del Caño,
# Máximo Kirchner, Marcos Galperin, Daniel Hadad.
CANDIDATOS_DEFAULT = [
    # La Libertad Avanza / oficialismo
    "Javier Milei",
    "Victoria Villarruel",
    "Patricia Bullrich",
    # Unión por la Patria / peronismo
    "Axel Kicillof",
    "Cristina Fernández de Kirchner",
    "Sergio Massa",
    "Juan Grabois",
    "Sergio Uñac",
    # PRO / Juntos por el Cambio
    "Mauricio Macri",
    # Provincias Unidas / centro
    "Juan Schiaretti",
    # Unión Cívica Radical
    "Facundo Manes",
    # Frente de Izquierda (FIT-U)
    "Myriam Bregman",
]

# Cuántos posts (como máximo) aporta cada término de búsqueda al corpus. Acota lo
# que trae la búsqueda general para dejar lugar a las búsquedas por candidato (si
# no, el término general coparía el corpus y volveríamos a ver un solo candidato).
POSTS_PER_TERM = 3
# Tope duro de posts que se mandan al clasificador electoral. Dimensionado para que
# TODOS los candidatos de la lista tengan lugar (general + 12 candidatos × 3 ≈ 39)
# y a la vez el análisis quede CÓMODO bajo el timeout de 120s: con 40 posts y lotes
# de 20 son solo 2 lotes de Gemini, que corren en UNA tanda (ver concurrency abajo).
# 52 posts / 3 lotes en 2 tandas llegaban a rozar los 120s cuando Gemini iba lento.
ELECTORAL_MAX_POSTS = 40
# El clasificador electoral corre en lotes de este tamaño (una llamada a Gemini por
# lote). 20 => 40 posts entran en 2 lotes, que caben en una sola tanda paralela.
ELECTORAL_BATCH_SIZE = 20

# ── Concurrencia (clave para no exceder el timeout de 120s del frontend) ──────
# Las búsquedas de SerpAPI y los lotes de Gemini son I/O bloqueante independiente.
# Antes corrían en SERIE (≈21 búsquedas + varios lotes uno atrás de otro) y el
# request tardaba >120s → timeout. Ahora corren en paralelo con pools acotados.
# SerpAPI: pool moderado para respetar el límite de concurrencia del plan.
FETCH_CONCURRENCY = 5
# Gemini: pool bajo para no gatillar los rate-limits del free-tier (run_with_rotation
# ya maneja 429/503, pero mejor no provocarlos con demasiadas llamadas simultáneas).
ELECTORAL_BATCH_CONCURRENCY = 2

# Redes para las búsquedas POR CANDIDATO. La opinión electoral vive sobre todo en X,
# así que las búsquedas por candidato van solo a X para ahorrar cuota de SerpAPI:
# con 3 redes seleccionadas + 6 candidatos, pasa de ~21 búsquedas a ~9 por análisis.
# La búsqueda GENERAL sí usa todas las redes seleccionadas por el usuario. Si el
# usuario no seleccionó X, los candidatos caen a las redes que sí eligió (para no
# quedarse sin buscarlos).
CANDIDATE_NETWORKS = ("twitter",)


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
   - "postura": la SEÑAL DIRECCIONAL del posteo hacia ese candidato (no solo la opinión explícita del autor: también la ventaja/desventaja que el posteo le atribuye). Exactamente uno de:
       * "a_favor": lo muestra FAVORABLE o EN VENTAJA — lo elogia/apoya/respalda, O reporta que lidera, puntea o tiene una intención de voto ALTA en una encuesta, O que gana/ganaría.
       * "en_contra": lo muestra DESFAVORABLE o EN DESVENTAJA — lo critica/ataca, O reporta que tiene una intención de voto MARGINAL o muy baja (claramente relegado) en una encuesta, O que pierde/perdería.
       * "neutro": mención meramente informativa, sin señal direccional clara, o con una intención de voto intermedia que no lo distingue.
   - "confianza": número entre 0 y 1 con tu certeza sobre esa señal.
   Si no hay candidatos, devolvé [].
   REGLA DE ENCUESTAS/SONDEOS: si la publicación es una encuesta o sondeo que reporta porcentajes de intención de voto, USÁ los porcentajes como señal (no lo trates como neutro por ser el autor imparcial): el/los candidato(s) puntero(s) o competitivo(s), con intención de voto claramente alta → "a_favor"; los de intención de voto marginal o muy baja (claramente fuera de la pelea) → "en_contra"; los intermedios → "neutro". NO devuelvas como candidatos las opciones que no son personas (voto en blanco, impugnado, indeciso, "no sabe / no contesta", "ninguno").
3. "cita": el fragmento textual breve del posteo que justifica la señal (o "" si no aplica).

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


def aggregate_net_sentiment(analysis: list[dict]) -> tuple[list[dict], int]:
    """Agrega las menciones por candidato. `pct` = SHARE DE MENCIONES (volumen de
    conversación), para que TODOS los candidatos detectados aparezcan proporcional
    a cuánto se habla de ellos.

    (Antes `pct` era el sentimiento neto a_favor−en_contra: colapsaba a un solo
    candidato cuando casi todo era neutro —caso típico con posteos-noticia—, que es
    justo lo que se veía como "un solo candidato". El sentimiento sigue disponible
    en pos/neg/neu para el desglose por barra del frontend.)

    Devuelve (candidatos, baja_confianza)."""
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
    suma_menciones = sum(e["menciones"] for e in entries)
    candidatos: list[dict] = []
    for e in entries:
        pct = (e["menciones"] / suma_menciones * 100) if suma_menciones else 0.0
        candidatos.append({
            "nombre": e["nombre"], "pct": round(pct, 1),
            "pos": e["pos"], "neg": e["neg"], "neu": e["neu"], "menciones": e["menciones"],
        })
    candidatos.sort(key=lambda c: c["pct"], reverse=True)
    return candidatos, baja_confianza


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
    # Agrupar por candidato_key -> {consultora: fila_completa} (para arrastrar la fuente).
    por_candidato: dict[str, dict[str, dict]] = {}
    for (cand_key, consultora), r in latest.items():
        por_candidato.setdefault(cand_key, {})[consultora] = r

    comparacion: list[dict] = []
    matched_csv_keys: set[str] = set()
    for c in candidatos:
        key = canonical_key(c["nombre"])
        # Recopilar consultoras que hacen match (exacto o alias de palabras).
        # Si dos CSV keys distintas matchean el mismo candidato de redes y
        # ambas tienen datos de la MISMA consultora, promediamos (no pisamos)
        # para evitar perder un valor en colisiones de alias multi-match.
        consultoras_data: dict[str, dict] = {}
        for csv_key, por_cons in por_candidato.items():
            if _keys_match(key, csv_key):
                matched_csv_keys.add(csv_key)
                for consultora, r in por_cons.items():
                    pct = r["porcentaje"]
                    if consultora in consultoras_data:
                        prev = consultoras_data[consultora]
                        prev["pct"] = round((prev["pct"] + pct) / 2, 1)
                        # conservar la fuente de la fila más reciente
                        if r.get("fecha", "") > prev.get("fecha", ""):
                            prev["fuente_url"] = r.get("fuente_url")
                            prev["fuente_titulo"] = r.get("fuente_titulo")
                            prev["fecha"] = r.get("fecha", "")
                    else:
                        consultoras_data[consultora] = {
                            "pct": pct,
                            "fuente_url": r.get("fuente_url"),
                            "fuente_titulo": r.get("fuente_titulo"),
                            "fecha": r.get("fecha", ""),
                        }
        consultoras = [
            {"consultora": nombre, "pct": d["pct"], "gap": round(c["pct"] - d["pct"], 1),
             "fuente_url": d.get("fuente_url"), "fuente_titulo": d.get("fuente_titulo"),
             "fecha": d.get("fecha", "")}
            for nombre, d in sorted(consultoras_data.items())
        ]
        if consultoras_data:
            promedio = round(sum(d["pct"] for d in consultoras_data.values()) / len(consultoras_data), 1)
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
            warnings.append(f"'{nombre}' aparece en los datos de consultoras pero no se detectó en redes.")
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


def _merge_pollster_rows(auto_rows: list[dict], manual_rows: list[dict]) -> list[dict]:
    """Une filas automáticas (con fuente) y del CSV. El CSV PISA al automático en
    conflicto (consultora, candidato canónico): es el dato vetado a mano."""
    merged: dict[tuple, dict] = {}
    for r in auto_rows:
        merged[(r["consultora"].strip().lower(), canonical_key(r["candidato"]))] = r
    for r in manual_rows:
        merged[(r["consultora"].strip().lower(), canonical_key(r["candidato"]))] = r
    return list(merged.values())


def run_boca_de_urna(keywords: list[str], networks: list[str], date: str | None,
                     country: str, pollster_csv: str, auto_consultoras: bool = False) -> dict:
    """Orquesta la boca de urna. Arma el corpus con una búsqueda general MÁS una
    búsqueda dedicada por cada candidato (para captar varias opiniones), corre el
    análisis electoral y arma el payload. Lanza UpstreamUnavailableError
    (Gemini/SerpAPI caído) o ValueError (CSV con header inválido)."""
    # 1) CSV primero: si el header es inválido, cortamos con ValueError (-> 400).
    pollster_rows, csv_warnings = parse_pollster_csv(pollster_csv)
    warnings = list(csv_warnings)

    # Consultoras automáticas (opcional): trae filas con fuente y las mergea con el
    # CSV. El CSV pisa al automático. Import diferido para evitar el ciclo con pollsters.
    if auto_consultoras:
        import pollsters
        auto_rows, auto_warnings = pollsters.fetch_pollster_rows(fecha_desde=date, country=country)
        warnings.extend(auto_warnings)
        pollster_rows = _merge_pollster_rows(auto_rows, pollster_rows)

    # 2) Corpus: búsqueda general + UNA por candidato (lista fija ∪ CSV), SIN el
    #    filtro de relevancia del monitor (fetch_raw_posts). El clasificador
    #    electoral decide qué es electoral. Esto es lo que garantiza que aparezcan
    #    varios candidatos y no uno solo. Cada término aporta hasta POSTS_PER_TERM
    #    posts para que la búsqueda general no cope el corpus.
    termino = " ".join([k for k in keywords if k and k.strip()]).strip() or "elecciones presidenciales"
    candidatos_buscar = build_candidate_search_list(pollster_rows)
    # Redes por candidato: X si el usuario la seleccionó; si no, las que sí eligió.
    cand_nets = [n for n in (networks or []) if n in CANDIDATE_NETWORKS] or (networks or [])
    # search_specs = lista de (término, redes). La general usa todas las redes; cada
    # candidato usa cand_nets (X) para ahorrar cuota. Dedup por texto de término.
    search_specs: list[tuple[str, list[str]]] = []
    vistos_terms: set[str] = set()
    for term, nets in [(termino, networks)] + [(f"{c} {termino}".strip(), cand_nets) for c in candidatos_buscar]:
        k = term.lower()
        if k and k not in vistos_terms:
            vistos_terms.add(k)
            search_specs.append((term, nets))

    def _fetch(spec: tuple[str, list[str]]) -> tuple[list[dict], bool]:
        term, nets = spec
        try:
            return normalizer.fetch_raw_posts(
                termino=term, fecha_desde=date, keywords=keywords,
                accounts=[], networks=nets, country=country,
            )
        except Exception as e:  # una búsqueda que rompe no debe tumbar el análisis
            print(f"ERROR fetch_raw_posts term='{term}': {e}")
            return [], True

    # Búsquedas en PARALELO (antes en serie -> >120s). ex.map preserva el orden de
    # search_specs, así el merge es determinístico (general primero, luego candidatos).
    with ThreadPoolExecutor(max_workers=min(FETCH_CONCURRENCY, len(search_specs))) as ex:
        resultados_por_termino = list(ex.map(_fetch, search_specs))

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
    candidatos, baja_conf = aggregate_net_sentiment(analysis)
    evidencia = build_evidence(analysis, posts_by_id)
    comparacion, comp_warnings = compare_vs_pollsters(candidatos, pollster_rows)
    warnings.extend(comp_warnings)

    if not candidatos:
        warnings.append("No se detectaron candidatos en las publicaciones analizadas.")
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
