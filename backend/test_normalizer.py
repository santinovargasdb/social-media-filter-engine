"""
Tests de la Capa 3 (normalizer): funciones puras y lógica de scoring/batch.

Cubren la lógica que más cambió (y regresionó) en el tiempo: formateo de URLs de
TikTok/Instagram, piso de score por modo, bifurcación del prompt y el batch de una
sola llamada a Gemini. Las llamadas a Gemini se mockean: no se pega a la red.

Correr desde backend/:  python -m pytest -q
"""
import normalizer as nz
import gemini_client as gc


# ── fetch_posts: paginación del monitor ───────────────────────────────────────
def test_fetch_posts_pagina_para_traer_mas(monkeypatch):
    """El monitor debe pedir varias páginas por red (no solo la primera ~10): ese
    techo era lo que hacía que ampliar el plazo de 2 a 9 meses casi no sumara posts."""
    llamadas = []

    def fake_search(termino, network, max_results=10, fecha_desde=None,
                    accounts=None, country="ar", pages=1):
        llamadas.append(pages)
        return [{"title": "t", "snippet": "SMATA",
                 "url": f"https://x.com/u/status/{network}", "date": ""}]

    monkeypatch.setattr(nz, "search_serpapi", fake_search)
    monkeypatch.setattr(nz, "_process_with_gemini", lambda serp, termino, smata_mode, keywords: [])
    nz._CACHE.clear()
    nz.fetch_posts("smata", networks=["twitter"])
    assert llamadas and all(pp > 1 for pp in llamadas)  # pidió más de una página


# ── fetch_raw_posts: paginación de la urna ────────────────────────────────────
def test_fetch_raw_posts_pagina(monkeypatch):
    """La Boca de Urna debe poder paginar sus búsquedas para llenar un corpus grande."""
    llamadas = []

    def fake_search(termino, network, max_results=10, fecha_desde=None,
                    accounts=None, country="ar", pages=1):
        llamadas.append(pages)
        return [{"title": "t", "snippet": "x",
                 "url": f"https://x.com/u/status/{network}", "date": ""}]

    monkeypatch.setattr(nz, "search_serpapi", fake_search)
    monkeypatch.setattr(nz, "_translate_query_for_country", lambda t, c: t)
    posts, up = nz.fetch_raw_posts("milei", networks=["twitter"], pages=2)
    assert llamadas == [2]


# ── _is_specific_post_url ─────────────────────────────────────────────────────
def test_is_specific_post_url_instagram():
    assert nz._is_specific_post_url("instagram", "https://www.instagram.com/p/Cabc/")
    assert nz._is_specific_post_url("instagram", "https://www.instagram.com/u/reel/Cxyz/")
    assert nz._is_specific_post_url("instagram", "https://www.instagram.com/u/tv/C1/")
    assert not nz._is_specific_post_url("instagram", "https://www.instagram.com/usuario/")


def test_is_specific_post_url_tiktok_y_twitter():
    assert nz._is_specific_post_url("tiktok", "https://www.tiktok.com/@u/video/123")
    assert not nz._is_specific_post_url("tiktok", "https://www.tiktok.com/@u")
    assert not nz._is_specific_post_url("tiktok", "https://www.tiktok.com/search?q=smata")
    assert nz._is_specific_post_url("twitter", "https://x.com/u/status/9")
    assert not nz._is_specific_post_url("twitter", "https://x.com/u")


def test_is_specific_post_url_vacio():
    assert not nz._is_specific_post_url("instagram", "")
    assert not nz._is_specific_post_url("tiktok", None)


# ── _tiktok_search_fallback_url ───────────────────────────────────────────────
def test_tiktok_fallback_autor_valido_va_a_perfil():
    assert nz._tiktok_search_fallback_url("elrulomanok", "smata") == "https://www.tiktok.com/@elrulomanok"
    # limpia el arroba inicial
    assert nz._tiktok_search_fallback_url("@user", "kw") == "https://www.tiktok.com/@user"


def test_tiktok_fallback_sin_autor_usa_keyword():
    assert nz._tiktok_search_fallback_url("", "salud") == "https://www.tiktok.com/search?q=salud"
    assert nz._tiktok_search_fallback_url("?", "paro automotriz") == "https://www.tiktok.com/search?q=paro%20automotriz"


def test_tiktok_fallback_sin_nada_es_none():
    assert nz._tiktok_search_fallback_url("", "") is None
    assert nz._tiktok_search_fallback_url("?", "") is None


def test_tiktok_fallback_nunca_devuelve_home_pelada():
    for a, k in [("u", "kw"), ("", "kw"), ("?", "x"), ("u", "")]:
        url = nz._tiktok_search_fallback_url(a, k)
        if url is not None:
            assert url.rstrip("/").lower() not in ("https://www.tiktok.com", "https://tiktok.com")


# ── _score_floor ──────────────────────────────────────────────────────────────
def test_score_floor_modo_amplio():
    assert nz._score_floor("twitter", smata_mode=False) == nz.SCORE_FLOOR_DEFAULT
    assert nz._score_floor("instagram", smata_mode=False) == nz.SCORE_FLOOR_DEFAULT
    assert nz._score_floor("tiktok", smata_mode=False) == 50  # tiktok siempre 50


def test_score_floor_modo_smata_nunca_baja_de_50():
    assert nz._score_floor("twitter", smata_mode=True) == 50
    assert nz._score_floor("instagram", smata_mode=True) == 50
    assert nz._score_floor("tiktok", smata_mode=True) == 50


# ── _level_from_score ─────────────────────────────────────────────────────────
def test_level_from_score():
    assert nz._level_from_score(100) == "alta"
    assert nz._level_from_score(70) == "alta"
    assert nz._level_from_score(69) == "media"
    assert nz._level_from_score(40) == "media"
    assert nz._level_from_score(39) == "baja"
    assert nz._level_from_score(0) == "baja"


# ── _find_matched_terms ───────────────────────────────────────────────────────
def test_find_matched_terms():
    assert nz._find_matched_terms("Paro de SMATA en Córdoba", ["smata", "paro"]) == ["smata", "paro"]
    assert nz._find_matched_terms("texto sin match", ["smata"]) == []
    assert nz._find_matched_terms("", ["smata"]) == []
    assert nz._find_matched_terms("hola", []) == []


# ── _parse_post_url ───────────────────────────────────────────────────────────
def test_parse_post_url_por_red():
    net, author, _, pid = nz._parse_post_url("https://x.com/eluser/status/123")
    assert (net, author, pid) == ("twitter", "eluser", "123")

    net, author, _, pid = nz._parse_post_url("https://www.tiktok.com/@creador/video/999")
    assert (net, author, pid) == ("tiktok", "creador", "999")

    net, _, _, _ = nz._parse_post_url("https://www.instagram.com/u/p/AAA/")
    assert net == "instagram"


def test_parse_post_url_usa_hint_cuando_no_resuelve():
    net, _, _, _ = nz._parse_post_url("https://desconocido.com/algo", hint_network="tiktok")
    assert net == "tiktok"


# ── _process_with_gemini: batch de 1 sola llamada + mapeo ─────────────────────
def _fake_scored(items):
    """Devuelve un _call_gemini_model fake que responde una lista fija."""
    def _fake(model, api_key, prompt, image=None):
        return (items, None)
    return _fake


def test_process_una_sola_llamada_para_todas_las_redes(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    llamadas = {"n": 0}

    def fake(model, api_key, prompt, image=None):
        llamadas["n"] += 1
        # Gemini responde por ID (espejo de los Post_N enviados).
        return ([
            {"id": "Post_0", "score": 90, "razon": "x"},
            {"id": "Post_1", "score": 80, "razon": "x"},
            {"id": "Post_2", "score": 75, "razon": "x"},
        ], None)

    monkeypatch.setattr(gc, "call_gemini_json", fake)
    serp = [
        {"title": "t", "snippet": "smata", "url": "https://x.com/u/status/1", "date": "", "network": "twitter"},
        {"title": "t", "snippet": "smata", "url": "https://www.instagram.com/u/p/AAA/", "date": "", "network": "instagram"},
        # TikTok con texto real (no basura): sobrevive la purga C.2 y testea la URL.
        {"title": "t", "snippet": "Asamblea de SMATA en la terminal automotriz", "url": "https://www.tiktok.com/@creador/video/123", "date": "", "network": "tiktok"},
    ]
    posts = nz._process_with_gemini(serp, "smata", smata_mode=False, keywords=["smata"])

    assert llamadas["n"] == 1, "debe ser UNA sola llamada a Gemini para las 3 redes"
    assert sorted(p["network"] for p in posts) == ["instagram", "tiktok", "twitter"]
    # El deep-link válido de TikTok se respeta (no se pisa con el perfil).
    tt = next(p for p in posts if p["network"] == "tiktok")
    assert tt["post_url"] == "https://www.tiktok.com/@creador/video/123"


def test_process_descarta_bajo_el_piso(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gc, "call_gemini_json",
                        _fake_scored([{"id": "Post_0", "score": 40, "razon": "x"}]))
    serp = [{"title": "", "snippet": "x", "url": "https://www.tiktok.com/@u/video/9", "date": "", "network": "tiktok"}]
    # En amplio el piso de TikTok es 50 -> 40 se descarta.
    assert nz._process_with_gemini(serp, "smata", smata_mode=False, keywords=[]) == []


def test_process_tiktok_sin_deeplink_cae_al_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gc, "call_gemini_json",
                        _fake_scored([{"id": "Post_0", "score": 90, "razon": "x"}]))
    serp = [{"title": "", "snippet": "Trabajadores de SMATA reclaman en la planta", "url": "https://www.tiktok.com/@creador", "date": "", "network": "tiktok"}]
    posts = nz._process_with_gemini(serp, "smata", smata_mode=False, keywords=[])
    # URL de perfil (sin /video/): no es deep-link, se reemplaza por búsqueda segura.
    assert posts[0]["post_url"].startswith("https://www.tiktok.com/search?q=")


# ── C.2 · Purga de basura de TikTok ───────────────────────────────────────────
def test_is_tiktok_garbage_detecta_ruido():
    assert nz._is_tiktok_garbage("")               # vacío
    assert nz._is_tiktok_garbage("   ")            # solo espacios
    assert nz._is_tiktok_garbage("smata")          # demasiado corto
    assert nz._is_tiktok_garbage("sonido original")           # solo música
    assert nz._is_tiktok_garbage("#fyp #parati #viral #foryou")  # solo hashtags
    assert nz._is_tiktok_garbage("1.2M views")     # solo conteo
    assert nz._is_tiktok_garbage("TikTok")         # boilerplate


def test_is_tiktok_garbage_respeta_contenido_real():
    assert not nz._is_tiktok_garbage("Asamblea de SMATA en la terminal automotriz")
    # música + texto real suficiente: NO es basura
    assert not nz._is_tiktok_garbage("Paro de SMATA en Córdoba, mecánicos en la calle · sonido original")


def test_process_purga_basura_tiktok(monkeypatch):
    """Un TikTok con snippet basura (solo música) se destruye aunque Gemini lo puntúe alto."""
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gc, "call_gemini_json", _fake_scored([
        {"id": "Post_0", "score": 95, "razon": "x"},
        {"id": "Post_1", "score": 95, "razon": "x"},
    ]))
    serp = [
        {"title": "", "snippet": "sonido original", "url": "https://www.tiktok.com/@u/video/1", "date": "", "network": "tiktok"},
        {"title": "", "snippet": "Paro de SMATA en la planta de Pacheco", "url": "https://www.tiktok.com/@u/video/2", "date": "", "network": "tiktok"},
    ]
    posts = nz._process_with_gemini(serp, "smata", smata_mode=False, keywords=[])
    # El de música se purga; el de contenido real sobrevive.
    assert len(posts) == 1
    assert posts[0]["post_url"] == "https://www.tiktok.com/@u/video/2"


def test_process_mapea_por_id_y_respeta_url_original(monkeypatch):
    """Gemini responde fuera de orden y con un id inexistente; el backend mapea
    por id, ignora el id basura y NUNCA toma la URL de la respuesta de Gemini."""
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gc, "call_gemini_json", _fake_scored([
        {"id": "Post_1", "score": 88, "razon": "x"},              # fuera de orden
        {"id": "Post_999", "score": 100, "razon": "inventado"},   # id que no enviamos
        {"id": "Post_0", "score": 70, "razon": "x"},
    ]))
    serp = [
        {"title": "", "snippet": "uno", "url": "https://x.com/a/status/1", "date": "", "network": "twitter"},
        {"title": "", "snippet": "dos", "url": "https://x.com/b/status/2", "date": "", "network": "twitter"},
    ]
    posts = nz._process_with_gemini(serp, "smata", smata_mode=False, keywords=[])
    by_url = {p["post_url"]: p for p in posts}
    # Solo 2 posts (el id inventado se ignora).
    assert len(posts) == 2
    # El score de Post_1 va al segundo serp (url .../2), no se mezcla.
    assert by_url["https://x.com/b/status/2"]["relevance_score"] == 88
    assert by_url["https://x.com/a/status/1"]["relevance_score"] == 70


def test_process_sin_api_key_devuelve_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    serp = [{"title": "", "snippet": "x", "url": "https://x.com/u/status/1", "date": "", "network": "twitter"}]
    assert nz._process_with_gemini(serp, "smata", smata_mode=False, keywords=[]) is None


# ── Fase E · Rotación a la API Key secundaria por cuota ───────────────────────
_SERP_OK = [{"title": "", "snippet": "Paro de SMATA en la planta de Pacheco",
             "url": "https://x.com/u/status/1", "date": "", "network": "twitter"}]


def test_process_rota_a_secundaria_en_429(monkeypatch):
    """La principal agota cuota (429); rota a la secundaria y el batch sale OK."""
    monkeypatch.setenv("GEMINI_API_KEY", "primary")
    monkeypatch.setenv("GEMINI_API_KEY_SECONDARY", "secondary")
    keys_usadas = []

    def fake(model, api_key, prompt, image=None):
        keys_usadas.append(api_key)
        if api_key == "primary":
            return (None, 429)  # cuota agotada en todos los modelos
        return ([{"id": "Post_0", "score": 90, "razon": "x"}], None)

    monkeypatch.setattr(gc, "call_gemini_json", fake)
    posts = nz._process_with_gemini(_SERP_OK, "smata", smata_mode=False, keywords=[])
    assert posts and posts[0]["relevance_score"] == 90
    assert "primary" in keys_usadas and "secondary" in keys_usadas  # rotó


def test_process_rota_tambien_en_503(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "primary")
    monkeypatch.setenv("GEMINI_API_KEY_SECONDARY", "secondary")

    def fake(model, api_key, prompt, image=None):
        if api_key == "primary":
            return (None, 503)
        return ([{"id": "Post_0", "score": 80, "razon": "x"}], None)

    monkeypatch.setattr(gc, "call_gemini_json", fake)
    posts = nz._process_with_gemini(_SERP_OK, "smata", smata_mode=False, keywords=[])
    assert posts and posts[0]["relevance_score"] == 80


def test_process_sin_secundaria_no_rota(monkeypatch):
    """Sin GEMINI_API_KEY_SECONDARY, una cuota agotada devuelve None (sin romper)."""
    monkeypatch.setenv("GEMINI_API_KEY", "primary")
    monkeypatch.delenv("GEMINI_API_KEY_SECONDARY", raising=False)
    monkeypatch.setattr(gc, "call_gemini_json", lambda m, k, p, image=None: (None, 429))
    assert nz._process_with_gemini(_SERP_OK, "smata", smata_mode=False, keywords=[]) is None


def test_process_no_rota_si_error_no_es_cuota(monkeypatch):
    """Un error que NO es de cuota (red/parseo) no dispara la rotación de llave."""
    monkeypatch.setenv("GEMINI_API_KEY", "primary")
    monkeypatch.setenv("GEMINI_API_KEY_SECONDARY", "secondary")
    keys_usadas = []

    def fake(model, api_key, prompt, image=None):
        keys_usadas.append(api_key)
        return (None, None)  # error de red/parseo, no cuota

    monkeypatch.setattr(gc, "call_gemini_json", fake)
    assert nz._process_with_gemini(_SERP_OK, "smata", smata_mode=False, keywords=[]) is None
    assert "secondary" not in keys_usadas  # NO rotó


# ── fetch_posts · Fallback red por red cuando el batch combinado falla ────────
def _post(net):
    return {"id": net, "network": net, "author": "a", "author_url": "", "text": "s",
            "date": "", "post_url": f"https://x/{net}", "relevance_score": 90,
            "relevance_level": "alta", "matched_terms": [], "video_url": None}


def test_fetch_posts_fallback_red_por_red(monkeypatch):
    """Si el batch combinado (multi-red) falla, reintenta red por red y recupera."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    nz._CACHE.clear()
    # Neutralizamos la Capa de Traducción para no pegarle a la red en este test.
    monkeypatch.setattr(nz, "_translate_query_for_country", lambda t, c: t)

    def fake_serp(termino, network, max_results, fecha_desde, accounts, country, pages=1):
        return [{"title": "t", "snippet": "s", "url": f"https://x/{network}", "date": ""}]

    def fake_proc(resultados, termino, smata_mode, keywords):
        nets = {r.get("network") for r in resultados}
        if len(nets) > 1:
            return None  # el batch combinado falla (timeout/truncado)
        return [_post(next(iter(nets)))]

    monkeypatch.setattr(nz, "search_serpapi", fake_serp)
    monkeypatch.setattr(nz, "_process_with_gemini", fake_proc)
    posts = nz.fetch_posts("noticias", networks=["twitter", "instagram", "tiktok"], country="jp")
    assert sorted(p["network"] for p in posts) == ["instagram", "tiktok", "twitter"]


def test_fetch_posts_fallback_parcial_no_rompe(monkeypatch):
    """Si en el fallback una red falla pero otra anda, devuelve resultados parciales
    (no 503)."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    nz._CACHE.clear()
    monkeypatch.setattr(nz, "_translate_query_for_country", lambda t, c: t)

    def fake_serp(termino, network, max_results, fecha_desde, accounts, country, pages=1):
        return [{"title": "t", "snippet": "s", "url": f"https://x/{network}", "date": ""}]

    def fake_proc(resultados, termino, smata_mode, keywords):
        nets = {r.get("network") for r in resultados}
        if len(nets) > 1:
            return None                      # batch combinado falla
        net = next(iter(nets))
        return None if net == "tiktok" else [_post(net)]  # tiktok sigue fallando

    monkeypatch.setattr(nz, "search_serpapi", fake_serp)
    monkeypatch.setattr(nz, "_process_with_gemini", fake_proc)
    posts = nz.fetch_posts("noticias", networks=["twitter", "tiktok"], country="jp")
    assert sorted(p["network"] for p in posts) == ["twitter"]  # parcial, sin romper


def test_fetch_posts_traduce_para_pais_no_hispano(monkeypatch):
    """En un país no hispanohablante, fetch_posts pasa el término TRADUCIDO a SerpAPI."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    nz._CACHE.clear()
    monkeypatch.setattr(nz, "_translate_query_for_country",
                        lambda t, c: "労働組合" if c == "jp" else t)
    capturado = {}

    def fake_serp(termino, network, max_results, fecha_desde, accounts, country, pages=1):
        capturado["termino"] = termino
        return [{"title": "t", "snippet": "s", "url": f"https://x/{network}", "date": ""}]

    def fake_proc(resultados, termino, smata_mode, keywords):
        capturado["termino_scoring"] = termino  # el scoring conserva el español
        return [_post("twitter")]

    monkeypatch.setattr(nz, "search_serpapi", fake_serp)
    monkeypatch.setattr(nz, "_process_with_gemini", fake_proc)
    nz.fetch_posts("sindicato", networks=["twitter"], country="jp")
    assert capturado["termino"] == "労働組合"          # SerpAPI recibe el nativo
    assert capturado["termino_scoring"] == "sindicato"  # scoring sigue en español


# ── Capa de Traducción Automática (búsquedas internacionales) ─────────────────
def test_translate_skip_pais_hispano_no_llama_gemini(monkeypatch):
    """Para países hispanohablantes NO se traduce ni se toca Gemini."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    llamadas = {"n": 0}
    monkeypatch.setattr(nz, "_gemini_generate",
                        lambda *a, **k: (llamadas.__setitem__("n", llamadas["n"] + 1), ("x", None))[1])
    for gl in ("ar", "es", "mx", "cl", "AR"):
        assert nz._translate_query_for_country("sindicato", gl) == "sindicato"
    assert llamadas["n"] == 0


def test_translate_pais_no_hispano_traduce(monkeypatch):
    """País no hispanohablante: devuelve el término traducido por Gemini."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    nz._TRANSLATION_CACHE.clear()
    monkeypatch.setattr(nz, "_gemini_generate",
                        lambda model, key, prompt, timeout=45: ('{"query": "労働組合"}', None))
    assert nz._translate_query_for_country("sindicato", "jp") == "労働組合"


def test_translate_cachea(monkeypatch):
    """La traducción se cachea: una segunda llamada no vuelve a pegarle a Gemini."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    nz._TRANSLATION_CACHE.clear()
    llamadas = {"n": 0}

    def fake_gen(model, key, prompt, timeout=45):
        llamadas["n"] += 1
        return '{"query": "Gewerkschaft"}', None

    monkeypatch.setattr(nz, "_gemini_generate", fake_gen)
    assert nz._translate_query_for_country("sindicato", "de") == "Gewerkschaft"
    assert nz._translate_query_for_country("sindicato", "de") == "Gewerkschaft"
    assert llamadas["n"] == 1  # la segunda salió del cache


def test_translate_falla_cae_al_termino_original(monkeypatch):
    """Si Gemini falla, la búsqueda cae al término en español (degradación segura)."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    nz._TRANSLATION_CACHE.clear()
    monkeypatch.setattr(nz, "_gemini_generate",
                        lambda model, key, prompt, timeout=45: (None, 500))
    assert nz._translate_query_for_country("sindicato", "ru") == "sindicato"


def test_translate_sin_api_key_cae_al_original(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    nz._TRANSLATION_CACHE.clear()
    assert nz._translate_query_for_country("sindicato", "jp") == "sindicato"


def test_translate_rota_a_secundaria_en_429(monkeypatch):
    """Si la key principal agota cuota (429), rota a la secundaria y traduce."""
    monkeypatch.setenv("GEMINI_API_KEY", "primary")
    monkeypatch.setenv("GEMINI_API_KEY_SECONDARY", "secondary")
    nz._TRANSLATION_CACHE.clear()

    def fake_gen(model, key, prompt, timeout=45):
        if key == "primary":
            return None, 429
        return '{"query": "профсоюз"}', None

    monkeypatch.setattr(nz, "_gemini_generate", fake_gen)
    assert nz._translate_query_for_country("sindicato", "ru") == "профсоюз"


# ── Bifurcación del prompt según smata_mode ───────────────────────────────────
def test_prompt_bifurca_estricto_vs_amplio(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    capturado = {}

    def fake(model, api_key, prompt, image=None):
        capturado["p"] = prompt
        return ([], None)

    monkeypatch.setattr(gc, "call_gemini_json", fake)
    serp = [{"title": "", "snippet": "x", "url": "https://x.com/u/status/1", "date": "", "network": "twitter"}]

    nz._process_with_gemini(serp, "salud", smata_mode=True, keywords=[])
    assert "DEBE estar ligado a SMATA" in capturado["p"]

    nz._process_with_gemini(serp, "salud", smata_mode=False, keywords=[])
    assert "NO exijas" in capturado["p"]


def test_prompt_blindado_ids_y_aislamiento(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    capturado = {}

    def fake(model, api_key, prompt, image=None):
        capturado["p"] = prompt
        return ([], None)

    monkeypatch.setattr(gc, "call_gemini_json", fake)
    serp = [
        {"title": "", "snippet": "a", "url": "https://x.com/u/status/1", "date": "", "network": "twitter"},
        {"title": "", "snippet": "b", "url": "https://x.com/u/status/2", "date": "", "network": "twitter"},
    ]
    nz._process_with_gemini(serp, "smata", smata_mode=True, keywords=[])
    p = capturado["p"]
    # IDs inyectados y formato espejo por id.
    assert "Post_0" in p and "Post_1" in p
    assert '"id"' in p
    # Regla de aislamiento explícita.
    assert "AISLAMIENTO" in p
    assert "PROHIBIDO" in p
