"""
Capa 3 — Normalización / Filtro e IA.

Responsabilidad: tomar los resultados crudos de la Capa 2 (fetcher), puntuarlos
con Gemini (UNA sola llamada, con IDs únicos y regla de aislamiento), parsear y
normalizar las URLs por red, aplicar el piso de score y devolver los posts en el
shape final. `fetch_posts` orquesta el flujo llamando a la Capa 2; esta capa NO
habla con SerpAPI directamente.

Si cambia un link de IG/TikTok, el prompt de Gemini o un criterio de filtrado
-> SOLO acá.
"""
import os
import re
import json
import time
from urllib.parse import quote

import requests

from fetcher import search_serpapi, SUPPORTED_NETWORKS
from gemini_client import (
    GEMINI_MODELS,
    GEMINI_RETRY_STATUSES,
    generate_raw,
    run_cascade,
    run_with_rotation,
)

# Alias de compat: _run_translation_cascade (abajo) llama a _gemini_generate por
# nombre de módulo, y sus tests parchean normalizer._gemini_generate. Mantener el
# alias preserva ese punto de parcheo sin duplicar la función.
_gemini_generate = generate_raw


class UpstreamUnavailableError(Exception):
    """SerpAPI o Gemini fallaron (rate limit / cuota / red) y no quedó ningún
    resultado utilizable. Permite al endpoint devolver un 503 con mensaje claro
    en vez de un listado vacío que se confunde con 'no hay resultados'."""


# ── Cache en memoria de fetch_posts ───────────────────────────────────────────
_CACHE_TTL_SECONDS = 600  # 10 minutos
_CACHE: dict[tuple, tuple[float, list[dict]]] = {}

_NETWORK_PROMPT_LABEL = {
    "twitter": "X (Twitter)",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

# Tope de resultados por red que se mandan a Gemini para reducir tokens
# y bajar la chance de tocar el rate limit con multi-red activo.
GEMINI_MAX_PER_NETWORK = 8

# ── Piso de score por red ─────────────────────────────────────────────────────
# Se aplica como filtro duro en Python después del scoring de Gemini.
# TikTok tiene umbral más alto porque sus snippets en Google son pobres
# (descripciones cortas / multi-idioma / cuentas extranjeras) y generan
# falsos positivos con scores moderados.
SCORE_FLOOR_DEFAULT = 30
SCORE_FLOOR_BY_NETWORK = {
    "tiktok": 50,
}


def _score_floor(network: str, smata_mode: bool) -> int:
    base = SCORE_FLOOR_BY_NETWORK.get(network, SCORE_FLOOR_DEFAULT)
    # En Modo SMATA (estricto) el piso nunca baja de 50. En modo amplio se usan
    # los pisos por red (30 default, TikTok 50 por la baja calidad de snippets).
    if smata_mode:
        return max(base, 50)
    return base


# ── Regex de URLs por red ─────────────────────────────────────────────────────
_TWITTER_URL_RE = re.compile(r"(?:x|twitter)\.com/([^/?#]+)/status/(\d+)", re.IGNORECASE)
_INSTAGRAM_USER_POST_RE = re.compile(r"instagram\.com/([^/?#]+)/(p|reel|tv)/([^/?#]+)", re.IGNORECASE)
_INSTAGRAM_POST_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)", re.IGNORECASE)
_TIKTOK_URL_RE = re.compile(r"tiktok\.com/@([^/?#]+)/video/(\d+)", re.IGNORECASE)

# Patrones que identifican un deep-link a un POST PUNTUAL (no a un perfil ni a la
# home de la plataforma). Si la URL original ya matchea, se respeta tal cual.
_SPECIFIC_POST_PATTERNS = {
    "twitter": re.compile(r"/status/\d+", re.IGNORECASE),
    "instagram": re.compile(r"/(?:p|reel|tv)/[^/?#]+", re.IGNORECASE),
    "tiktok": re.compile(r"/video/\d+", re.IGNORECASE),
}


def _is_specific_post_url(network: str, url: str) -> bool:
    """True si la URL apunta a un post puntual indexado (no perfil ni home).

    Instagram: /p/, /reel/, /tv/ · TikTok: /video/ · X/Twitter: /status/.
    """
    if not url:
        return False
    pat = _SPECIFIC_POST_PATTERNS.get(network)
    return bool(pat and pat.search(url))


# Paths de IG que parecen "user" en la URL pero no lo son
_IG_RESERVED_PATHS = {
    "p", "reel", "tv", "explore", "accounts", "direct",
    "stories", "web", "developer", "about", "legal",
}


def _parse_twitter_url(url: str) -> tuple[str, str, str]:
    m = _TWITTER_URL_RE.search(url or "")
    if not m:
        return "", "", url or ""
    user, post_id = m.group(1), m.group(2)
    return user, f"https://x.com/{user}", post_id


def _parse_instagram_url(url: str) -> tuple[str, str, str]:
    m = _INSTAGRAM_USER_POST_RE.search(url or "")
    if m and m.group(1).lower() not in _IG_RESERVED_PATHS:
        user, post_id = m.group(1), m.group(3)
        return user, f"https://instagram.com/{user}", post_id
    m = _INSTAGRAM_POST_RE.search(url or "")
    if m:
        return "", "", m.group(1)
    return "", "", url or ""


def _parse_tiktok_url(url: str) -> tuple[str, str, str]:
    m = _TIKTOK_URL_RE.search(url or "")
    if not m:
        return "", "", url or ""
    user, video_id = m.group(1), m.group(2)
    return user, f"https://tiktok.com/@{user}", video_id


def _tiktok_search_fallback_url(author: str, keyword: str) -> str | None:
    """Link de TikTok robusto, a prueba de fallos.

    SerpAPI a veces devuelve la URL del último video del creador en lugar del
    post que matcheó en texto. Además, meter "@usuario keyword" en el buscador
    interno marea al algoritmo (muestra perfiles sueltos o videos random en
    Populares). Por eso evitamos la búsqueda mixta:

    1. Autor válido -> perfil directo del creador:  tiktok.com/@usuario
                       (Prensa audita su feed al toque).
    2. Sin autor (vacío o "?") -> búsqueda global SOLO por la keyword, sin arroba:
                       tiktok.com/search?q=keyword
    3. Nada de lo anterior -> None (el caller conserva la URL original).

    NUNCA devuelve "tiktok.com" sin path/parámetros (eso mandaba al FYP random).
    """
    clean_author = (author or "").lstrip("@").strip()
    # SerpAPI deja "?" (u otros placeholders) cuando no logró extraer el autor.
    if clean_author in ("", "?", "-"):
        clean_author = ""
    clean_kw = (keyword or "").strip()

    if clean_author:
        return f"https://www.tiktok.com/@{quote(clean_author, safe='')}"
    if clean_kw:
        return f"https://www.tiktok.com/search?q={quote(clean_kw)}"
    # Sin autor ni keyword no hay forma de armar un link con sentido.
    return None


def _detect_network_from_url(url: str) -> str | None:
    u = (url or "").lower()
    if "instagram.com/" in u:
        return "instagram"
    if "tiktok.com/" in u:
        return "tiktok"
    if "x.com/" in u or "twitter.com/" in u:
        return "twitter"
    return None


def _parse_post_url(url: str, hint_network: str | None = None) -> tuple[str, str, str, str]:
    """Devuelve (network, author, author_url, post_id). Usa hint_network si la URL no resuelve."""
    network = _detect_network_from_url(url) or hint_network or "twitter"
    if network == "twitter":
        a, au, pid = _parse_twitter_url(url)
    elif network == "instagram":
        a, au, pid = _parse_instagram_url(url)
    elif network == "tiktok":
        a, au, pid = _parse_tiktok_url(url)
    else:
        a, au, pid = "", "", url or ""
    return network, a, au, pid


def _level_from_score(score: int) -> str:
    if score >= 70:
        return "alta"
    if score >= 40:
        return "media"
    return "baja"


# ── C.2 · Purga de basura de TikTok ───────────────────────────────────────────
# Los snippets de TikTok en Google suelen ser ruido que ensucia la grilla de
# Prensa: posteos vacíos, solo "sonido original"/música, solo hashtags/menciones,
# o conteos sueltos (likes/views). Los purgamos en código ANTES de mostrarlos.
_TIKTOK_MUSIC_RE = re.compile(
    r"(sonido original|original sound|canci[oó]n original|música original|♬|🎵|🎶)",
    re.IGNORECASE,
)
_TIKTOK_BOILERPLATE_RE = re.compile(
    r"^\s*(tiktok|ver en tiktok|mira(?:r)?\s+(?:el|este)\s+video|"
    r"watch .*?on tiktok|videos?\s+de\s+tiktok|explora\s+los\s+videos?)\s*[.·:\-]*\s*$",
    re.IGNORECASE,
)
_TIKTOK_COUNT_ONLY_RE = re.compile(
    r"^[\s\d.,kKmM]+\s*(likes?|me gusta|views?|vistas|reproducciones|"
    r"followers?|seguidores|fans|comentarios?|comments?)?\s*$",
    re.IGNORECASE,
)


def _is_tiktok_garbage(snippet: str, title: str = "") -> bool:
    """True si el contenido de un resultado de TikTok es ruido sin valor de prensa."""
    text = (snippet or "").strip()
    if not text:
        return True
    if len(text) < 12:  # demasiado corto para tener valor informativo
        return True
    low = text.lower()
    # Música/sonido original con muy poco texto alfabético real → basura.
    if _TIKTOK_MUSIC_RE.search(low):
        alpha = re.sub(r"[^a-záéíóúñ ]", "", low).strip()
        if len(alpha) < 25:
            return True
    if _TIKTOK_BOILERPLATE_RE.match(low):
        return True
    if _TIKTOK_COUNT_ONLY_RE.match(text):
        return True
    # Solo hashtags y/o menciones, sin ninguna palabra real (3+ letras).
    sin_tags = re.sub(r"[#@]\S+", "", text).strip()
    if not re.search(r"[a-záéíóúñ]{3,}", sin_tags, re.IGNORECASE):
        return True
    return False


def _find_matched_terms(text: str, keywords: list[str]) -> list[str]:
    if not text or not keywords:
        return []
    text_lower = text.lower()
    seen = []
    for k in keywords:
        kl = (k or "").strip().lower()
        if kl and kl in text_lower and kl not in [s.lower() for s in seen]:
            seen.append(k)
    return seen


# ── Capa de Traducción Automática (búsquedas internacionales) ─────────────────
# Cuando el país de búsqueda NO es hispanohablante (Japón, Alemania, Rusia, etc.),
# las keywords en español jamás matchean contenido nativo: el monitor devolvía
# siempre "No se encontraron resultados". ANTES de armar la query de SerpAPI,
# traducimos el término al idioma nativo del país con Gemini, manteniendo el
# sentido técnico/sindical y de redes sociales y devolviéndolo en su alfabeto
# nativo (cirílico, japonés, hangul, etc.).
#
# El término traducido se usa SOLO como 'q' de SerpAPI. El scoring de Gemini sigue
# recibiendo el término en español (para evaluar bien la intención/sentimiento) y
# los matched_terms siguen comparando las keywords en español contra el texto que
# la Capa 3 ya retraduce al español (campo "texto", B.5). Si la traducción falla
# por cualquier motivo, caemos al término original: peor caso, busca en español
# como antes (degradación segura, nunca rompe la búsqueda).

# Países hispanohablantes: para ellos NO traducimos (ya buscan en el idioma
# correcto y gastar cuota de Gemini no aportaría nada). Alineado con los códigos
# que mapean a hl='es' en la Capa 2.
_SPANISH_SPEAKING_GL = {
    "ar", "es", "mx", "cl", "uy", "co", "pe", "ve", "bo", "py", "ec",
    "gt", "hn", "sv", "ni", "cr", "pa", "do", "cu", "pr",
}

# Cache de traducciones (term, gl) -> término traducido. Las traducciones no
# cambian y la cardinalidad es chica (pocos términos × pocos países), así que
# un cache permanente ahorra cuota de Gemini en búsquedas internacionales repetidas.
_TRANSLATION_CACHE: dict[tuple[str, str], str] = {}


def _build_translation_prompt(termino: str, gl: str) -> str:
    """Prompt para que Gemini traduzca el término de búsqueda al idioma nativo del
    país (código ISO 'gl'), con criterio de redes sociales y terminología gremial."""
    return (
        "Sos un traductor experto en redes sociales y en terminología sindical, "
        "gremial y laboral.\n"
        f"Traducí los siguientes términos de búsqueda del español al idioma nativo "
        f"del país cuyo código ISO 3166-1 alpha-2 es '{gl}'.\n\n"
        "REGLAS:\n"
        "1. NO hagas una traducción literal: usá los términos REALES y naturales que "
        "un hablante nativo de ese país usaría en sus publicaciones en redes sociales. "
        "Por ejemplo, 'sindicato' debe traducirse al término que de verdad usan los "
        "nativos para referirse a un sindicato/gremio en ese país.\n"
        "2. Mantené el sentido técnico, sindical/gremial y de redes sociales.\n"
        "3. Devolvé el resultado en el alfabeto nativo correspondiente (cirílico, "
        "japonés, hangul, etc.), NO transliterado al alfabeto latino.\n"
        "4. Conservá tal cual los nombres propios, marcas y siglas (por ejemplo SMATA).\n\n"
        f'Términos a traducir: "{termino}"\n\n'
        "Devolvé ÚNICAMENTE un JSON válido, sin texto adicional ni bloques de código, "
        'con este formato EXACTO:\n'
        '{ "query": "términos traducidos separados por espacios" }'
    )


def _run_translation_cascade(prompt: str, api_key: str) -> tuple[str | None, int | None]:
    """Recorre la cascada GEMINI_MODELS con UNA api_key para traducir. Devuelve
    (query_traducida, last_status). Mismo patrón de fallback por modelo que el
    scoring: solo prueba el siguiente modelo si el anterior dio 429/503/404."""
    status: int | None = None
    for idx, model in enumerate(GEMINI_MODELS):
        raw, status = _gemini_generate(model, api_key, prompt, timeout=30)
        if raw is not None:
            try:
                data = json.loads(raw)
                query = (data.get("query") or "").strip() if isinstance(data, dict) else ""
            except Exception as e:
                print(f"ERROR Gemini traducción[{model}]: parseo JSON falló — {e}")
                query = ""
            if query:
                if idx > 0:
                    print(f"DEBUG Gemini traducción: fallback EXITOSO con {model}")
                return query, status
            # Respuesta OK pero sin 'query' usable: no es error de cuota, no insistas.
            return None, status
        if status not in GEMINI_RETRY_STATUSES:
            return None, status
        if idx + 1 < len(GEMINI_MODELS):
            print(f"DEBUG Gemini traducción: HTTP {status} en {model}, probando fallback {GEMINI_MODELS[idx + 1]}")
    return None, status


def _translate_query_for_country(termino: str, country: str) -> str:
    """Capa de Traducción Automática: traduce el término de búsqueda al idioma nativo
    del país (cuando NO es hispanohablante) para armar la query de SerpAPI. Cae al
    término original ante cualquier fallo (degradación segura)."""
    gl = (country or "ar").strip().lower()
    termino = (termino or "").strip()
    if not termino or gl in _SPANISH_SPEAKING_GL:
        return termino

    cache_key = (termino, gl)
    cached = _TRANSLATION_CACHE.get(cache_key)
    if cached is not None:
        print(f"DEBUG traducción: cache HIT ({gl}) '{termino}' -> '{cached}'")
        return cached

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY no configurada — sin traducción internacional, busco en español.")
        return termino

    prompt = _build_translation_prompt(termino, gl)

    # Cascada con la API Key PRINCIPAL.
    query, status = _run_translation_cascade(prompt, api_key)

    # Misma contingencia que el scoring (Fase E): si la cuota principal se agotó
    # (429/503), rotamos a la API Key SECUNDARIA y reintentamos la traducción.
    if query is None and status in (429, 503):
        secondary_key = os.getenv("GEMINI_API_KEY_SECONDARY", "")
        if secondary_key:
            print("Cuota principal agotada en traducción. Rotando a la API de SMATA...")
            query, status = _run_translation_cascade(prompt, secondary_key)

    if not query:
        print(f"DEBUG traducción ({gl}): falló (status={status}) — uso el término original en español.")
        return termino

    print(f"DEBUG traducción internacional ({gl}): '{termino}' -> '{query}'")
    _TRANSLATION_CACHE[cache_key] = query
    return query


def _process_with_gemini(
    resultados: list[dict],
    termino: str,
    smata_mode: bool,
    keywords: list[str],
) -> list[dict] | None:
    """
    Pide a Gemini scores de relevancia para TODAS las redes en UNA sola llamada
    (cada resultado trae su campo "network") y mapea cada uno al shape PostOut.
    Una sola llamada en vez de una por red reduce ~66% el consumo de cuota.
    Retorna None ante errores upstream (auth, 503, parseo) para que fetch_posts
    no cachee un vacío espurio. [] indica vacío legítimo.

    Cascada de modelos GEMINI_MODELS con fallback en 429/503, y rotación a la API
    Key secundaria (GEMINI_API_KEY_SECONDARY) si la cuota principal se agota.
    """
    # El tope por red ya se aplicó en fetch_posts antes de mergear; acá usamos
    # el lote combinado tal cual.
    resultados_limitados = resultados
    redes_presentes = sorted({r.get("network", "") for r in resultados if r.get("network")})
    redes_label = ", ".join(_NETWORK_PROMPT_LABEL.get(n, n) for n in redes_presentes) or "redes sociales"

    # Inyección de IDs únicos e inequívocos ("Post_0", "Post_1", ...). El scoring
    # vuelve a asociarse por ese ID (no por URL), para que Gemini no pueda mezclar
    # publicaciones ni alterar la asociación. by_id mapea ID -> resultado crudo.
    by_id: dict[str, dict] = {}
    items_para_prompt: list[dict] = []
    for i, r in enumerate(resultados_limitados):
        pid = f"Post_{i}"
        by_id[pid] = r
        items_para_prompt.append({
            "id": pid,
            "network": r.get("network", ""),
            "title": r.get("title", ""),
            "snippet": r.get("snippet", ""),
            "url": r.get("url", ""),
            "date": r.get("date", ""),
        })
    resultados_text = json.dumps(items_para_prompt, ensure_ascii=False, indent=2)

    # ── Bifurcación del criterio según el switch "Modo SMATA" ──────────────────
    if smata_mode:
        # MODO SMATA (hiper-estricto): el contenido DEBE estar ligado a SMATA, a la
        # industria automotriz o a los mecánicos en Argentina. Cualquier desvío
        # del tema (p. ej. contenido de Indonesia u otro país sin relación) → 0.
        system_role = (
            "Sos un asistente de análisis de redes sociales para el sindicato SMATA "
            "(sector automotriz argentino)."
        )
        criterio = (
            "1. Analizá cada resultado y determiná si es relevante para SMATA y el "
            "sector automotriz/sindical argentino.\n"
            "2. El contenido DEBE estar ligado a SMATA, a la industria automotriz o a "
            "los mecánicos en Argentina. Cualquier desvío del tema (por ejemplo "
            "contenido de Indonesia u otro país sin relación) va a score 0.\n"
            "3. Asigná un score del 0 al 100 según su relevancia.\n"
            "4. Solo incluí posts con score >= 50."
        )
        tiktok_warning = ""
        if "tiktok" in redes_presentes:
            tiktok_warning = (
                "\nATENCIÓN — REGLAS ADICIONALES PARA LOS RESULTADOS CON network='tiktok':\n"
                "Los snippets de TikTok en Google suelen ser pobres, en otros idiomas o "
                "de cuentas internacionales sin relación con el sindicato SMATA ni con la "
                "industria automotriz argentina. Aplicá estas reglas estrictas:\n"
                "- Si el snippet NO menciona explícitamente al sindicato SMATA, a la "
                "industria automotriz argentina, o a un actor argentino del rubro "
                "(empresas como Ford/Volkswagen/Toyota Argentina, dirigentes gremiales, "
                "políticos del sector, etc.) → score 0-15.\n"
                "- Posts en otro idioma que no sea español rioplatense → score 0.\n"
                "- Cuentas o handles que parezcan extranjeros o no tengan contexto "
                "argentino → score 0-10.\n"
                "- No asumas relevancia si el texto es muy corto o ambiguo: en caso de "
                "duda, score bajo.\n"
            )
    else:
        # MODO AMPLIO (Monitor de Prensa): dejá pasar lo relevante a nivel político,
        # gremial, social, económico o cultural en Argentina, SIN exigir SMATA.
        system_role = (
            "Sos un monitor de prensa amplio para un sindicato argentino. Analizás "
            "tendencias en redes sociales del ámbito argentino."
        )
        criterio = (
            "1. Dejá pasar cualquier publicación que coincida con la keyword y que sea "
            "relevante a nivel político, gremial, social, económico o cultural en "
            "Argentina. NO exijas que el post mencione a SMATA.\n"
            "2. Asigná un score del 0 al 100 según esa relevancia informativa.\n"
            "3. Penalizá con score 0 ÚNICAMENTE: spam evidente, bots, contenido "
            "internacional fuera de LATAM, o posteos completamente vacíos de valor "
            "informativo.\n"
            "4. Incluí todos los posts que superen ese criterio amplio."
        )
        tiktok_warning = ""
        if "tiktok" in redes_presentes:
            tiktok_warning = (
                "\nNOTA PARA LOS RESULTADOS CON network='tiktok':\n"
                "Los snippets de TikTok en Google suelen ser pobres o multi-idioma. "
                "Penalizá con score bajo solo el contenido internacional fuera de LATAM, "
                "el spam o los videos sin valor informativo. NO exijas mención de SMATA.\n"
            )

    prompt = f"""{system_role}

Vas a recibir una lista de publicaciones en formato JSON sobre publicaciones en {redes_label} relacionadas con el término: "{termino}". Cada publicación tiene un "id" único ("Post_0", "Post_1", ...), más "title", "snippet", "url", "date" y "network" (twitter, instagram o tiktok).

REGLA DE AISLAMIENTO (OBLIGATORIA): Debés evaluar cada publicación de forma totalmente AISLADA e INDEPENDIENTE, aplicando exactamente los mismos criterios de filtrado a todas. Está TERMINANTEMENTE PROHIBIDO que el contexto, el tema o el puntaje de una publicación influya en el de otra. Tratá cada "id" como un caso separado, como si lo analizaras solo. No agrupes ni promedies ni "contagies" relevancia entre publicaciones.

Criterios de evaluación (idénticos para cada publicación):
{criterio}{tiktok_warning}

REGLAS ADICIONALES (OBLIGATORIAS para TODAS las publicaciones):
A) INTENCIÓN / SENTIMIENTO DE LA BÚSQUEDA: Si el término buscado "{termino}" tiene una carga de sentimiento explícita (por ejemplo una crítica como "smata es una mierda", o un elogio claro), NO alcanza con que el posteo contenga la palabra clave: evaluá la INTENCIÓN GLOBAL del posteo. Si una publicación coincide con la palabra clave pero expresa una carga emocional OPUESTA a la del término buscado (el término es una crítica y el posteo tiene tono positivo/elogioso, o viceversa), asignale score 0. Solo deben pasar las publicaciones que cumplen la premisa EXACTA de la búsqueda, incluida su intención. Si el término es neutro (sin carga de sentimiento), ignorá esta regla y puntuá normalmente.
B) TRADUCCIÓN AL ESPAÑOL: Por cada publicación devolvé también un campo "texto". Si el texto original (title/snippet) está en un idioma distinto al español, traducilo de forma precisa y natural al español dentro del campo "texto", manteniendo el sentido original del posteo. Si ya está en español, devolvé en "texto" el contenido tal cual (limpio, sin recortar el sentido).

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código) que sea un ESPEJO EXACTO de los IDs recibidos: un objeto por cada publicación enviada, con su mismo "id". No agregues ni omitas ninguno. Formato exacto:
[
  {{ "id": "Post_0", "score": 90, "texto": "texto del posteo en español", "razon": "breve justificación del puntaje" }},
  {{ "id": "Post_1", "score": 0, "texto": "texto del posteo en español", "razon": "breve justificación del puntaje" }}
]

Publicaciones a evaluar:
{resultados_text}"""

    # Transporte compartido: cascada de modelos + rotación a la key secundaria.
    scored, status = run_with_rotation(prompt)
    if scored is None:
        return None

    posts: list[dict] = []
    tiktok_garbage = 0
    for item in scored:
        # Mapeo de retorno por ID: recuperamos el post crudo ORIGINAL por su id.
        # La URL sale de NUESTROS datos (no de lo que devuelve Gemini), así la
        # asociación es inequívoca y la IA no puede alterar el link.
        pid = item.get("id", "") or ""
        src = by_id.get(pid)
        if src is None:
            # id inexistente (Gemini lo inventó o repitió): lo ignoramos.
            continue
        try:
            score = int(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0

        post_url = src.get("url", "") or ""
        snippet_src = src.get("snippet", "") or src.get("title", "")
        # B.5: si Gemini devolvió "texto" (traducido al español cuando el original
        # estaba en otro idioma), lo usamos como texto a mostrar. Fallback al
        # snippet crudo si la IA no lo devolvió.
        texto_ia = (item.get("texto") or "").strip()
        snippet = texto_ia or snippet_src
        date = src.get("date", "") or ""
        network, author, author_url, post_id = _parse_post_url(post_url, hint_network=src.get("network"))

        # C.2: purga de basura de TikTok (vacíos, solo música, solo hashtags,
        # conteos sueltos). Se evalúa sobre el texto crudo de SerpAPI, no sobre la
        # traducción, para detectar el ruido de origen.
        if network == "tiktok" and _is_tiktok_garbage(snippet_src, src.get("title", "")):
            tiktok_garbage += 1
            continue

        # Prioridad ABSOLUTA al post real: si la URL ya es un deep-link válido al
        # posteo (/p/, /reel/, /tv/ en IG; /video/ en TikTok; /status/ en X), la
        # respetamos sin pisarla. Solo cuando la URL está vacía, rota, apunta a la
        # home/perfil o no se pudo extraer el autor ("?"), caemos al fallback de
        # TikTok (perfil del creador o búsqueda global por keyword).
        link = post_url
        if not _is_specific_post_url(network, post_url) and network == "tiktok":
            fallback = _tiktok_search_fallback_url(author, termino)
            if fallback:
                link = fallback

        posts.append({
            "id": post_id or post_url,
            "network": network,
            "author": author,
            "author_url": author_url,
            "text": snippet,
            "date": date,
            "post_url": link,
            "relevance_score": score,
            "relevance_level": _level_from_score(score),
            "matched_terms": _find_matched_terms(snippet, keywords),
            "video_url": None,
        })

    if tiktok_garbage:
        print(f"DEBUG purga TikTok (C.2): {tiktok_garbage} posteos basura (vacío/música/hashtags/conteos) destruidos")

    # Filtro duro por piso de score: defiende contra falsos positivos que el
    # prompt no logró bajar (especialmente en TikTok, donde aplicamos piso 50).
    filtered: list[dict] = []
    dropped_by_floor: dict[str, int] = {}
    for p in posts:
        floor = _score_floor(p["network"], smata_mode)
        if p["relevance_score"] >= floor:
            filtered.append(p)
        else:
            dropped_by_floor[p["network"]] = dropped_by_floor.get(p["network"], 0) + 1
    if dropped_by_floor:
        details = ", ".join(f"{n}:{c}" for n, c in dropped_by_floor.items())
        print(f"DEBUG filtro score floor: descartados por red {{{details}}}")

    return filtered


def fetch_posts(
    termino: str,
    fecha_desde: str = None,
    smata_mode: bool = False,
    keywords: list[str] | None = None,
    accounts: list[str] | None = None,
    networks: list[str] | None = None,
    country: str = "ar",
) -> list[dict]:
    """
    Orquestador principal (Capa 3). Pide los resultados crudos a la Capa 2
    (fetcher.search_serpapi) por cada red, los mergea y los puntúa con Gemini en
    una sola llamada. Devuelve la lista de posts normalizados.
    """
    nets = [n for n in (networks or []) if n in SUPPORTED_NETWORKS]
    if not nets:
        nets = ["twitter"]  # fallback seguro si el front no manda nada válido

    print(f"DEBUG: fetch_posts termino='{termino}' redes={nets} smata_mode={smata_mode}")

    # Barrido de entradas vencidas: evita que _CACHE crezca sin límite con
    # búsquedas que no se repiten (la instancia de Render vive mucho tiempo).
    now = time.time()
    for k in [k for k, (ts, _) in _CACHE.items() if now - ts >= _CACHE_TTL_SECONDS]:
        del _CACHE[k]

    cache_key = (
        termino,
        fecha_desde,
        smata_mode,
        tuple(sorted(keywords or [])),
        tuple(sorted(accounts or [])),
        tuple(sorted(nets)),
        (country or "ar").strip().lower(),
    )
    cached = _CACHE.get(cache_key)
    if cached is not None:
        ts, data = cached
        if time.time() - ts < _CACHE_TTL_SECONDS:
            print(f"DEBUG: cache HIT para {cache_key}")
            return data
        del _CACHE[cache_key]

    all_posts: list[dict] = []
    any_upstream_error = False
    total_serp_results = 0

    # 0) Capa de Traducción Automática: para países NO hispanohablantes traducimos
    #    el término al idioma nativo ANTES de pegarle a SerpAPI (si no, buscar en
    #    Japón/Alemania/Rusia con keywords en español nunca trae resultados). El
    #    scoring de Gemini sigue usando 'termino' en español (intención/sentimiento)
    #    y los matched_terms comparan las keywords en español contra el texto que la
    #    Capa 3 retraduce al español (B.5). Ante cualquier fallo cae al término original.
    termino_busqueda = _translate_query_for_country(termino, country)

    # 1) SerpAPI por red (su cuota es amplia), aplicando el tope por red ANTES de
    #    mergear. Cada resultado se etiqueta con su "network".
    serp_merged: list[dict] = []
    for net in nets:
        resultados = search_serpapi(
            termino_busqueda,
            network=net,
            max_results=10,
            fecha_desde=fecha_desde,
            accounts=accounts,
            country=country,
        )
        if resultados is None:
            any_upstream_error = True
            continue
        if not resultados:
            continue
        lote = resultados[:GEMINI_MAX_PER_NETWORK]
        if len(lote) < len(resultados):
            print(f"DEBUG Gemini[{net}]: lote reducido {len(resultados)} -> {len(lote)}")
        for r in lote:
            item = dict(r)
            item["network"] = net
            serp_merged.append(item)
        total_serp_results += len(lote)

    # 2) UNA sola llamada a Gemini para TODAS las redes juntas (en vez de una por
    #    red): reduce ~66% el consumo de la cuota free-tier.
    if serp_merged:
        posts = _process_with_gemini(serp_merged, termino, smata_mode, keywords or [])
        if posts is not None:
            all_posts.extend(posts)
        else:
            # El batch combinado falló (None). Caso típico: búsquedas
            # internacionales donde la traducción (B.5) infla la respuesta de
            # Gemini y dispara timeout o JSON truncado; un timeout NO rota a la
            # llave secundaria (status None). Antes de rendirnos con un 503,
            # reintentamos RED POR RED: lotes más chicos (máx GEMINI_MAX_PER_NETWORK)
            # son más rápidos y menos propensos a truncarse. Si alguna red sale OK,
            # el usuario ve resultados parciales en vez de un error.
            print("DEBUG: batch combinado a Gemini falló — fallback red por red.")
            redes_en_lote = sorted({r.get("network") for r in serp_merged})
            recuperado_alguna = False
            for net in redes_en_lote:
                lote_net = [r for r in serp_merged if r.get("network") == net]
                if not lote_net:
                    continue
                posts_net = _process_with_gemini(lote_net, termino, smata_mode, keywords or [])
                if posts_net is None:
                    any_upstream_error = True
                    print(f"DEBUG: fallback Gemini[{net}] también falló.")
                else:
                    recuperado_alguna = True
                    all_posts.extend(posts_net)
                    print(f"DEBUG: fallback Gemini[{net}] recuperó {len(posts_net)} posts.")
            if recuperado_alguna:
                print("DEBUG: fallback red por red recuperó resultados parciales.")

    print(f"DEBUG: total posts pre-dedupe = {len(all_posts)} (sobre {total_serp_results} resultados SerpAPI, upstream_error={any_upstream_error})")

    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for p in all_posts:
        url = p.get("post_url") or ""
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        deduped.append(p)
    if len(deduped) != len(all_posts):
        print(f"DEBUG: dedupe quitó {len(all_posts) - len(deduped)} duplicados por post_url")
    all_posts = deduped

    # Si hubo error upstream (SerpAPI/Gemini) y NO quedó ningún post utilizable,
    # lo señalamos como 503 en vez de devolver [] (que se ve como "no hay nada").
    # El caso típico es el rate limit / cuota agotada de Gemini free-tier.
    if any_upstream_error and not all_posts:
        raise UpstreamUnavailableError(
            "El analizador de IA (Gemini) o el buscador (SerpAPI) no respondieron, "
            "probablemente por límite de cuota. Reintentá en un minuto."
        )

    # Política de cache: no cachear si SerpAPI o Gemini fallaron en cualquier red.
    # Así un reintento posterior puede recuperar las redes faltantes en vez de
    # quedar pegado con un resultado parcial durante 10 min.
    if any_upstream_error:
        print("DEBUG: no se cachea (error upstream en alguna red — SerpAPI o Gemini)")
    else:
        _CACHE[cache_key] = (time.time(), all_posts)
    return all_posts
