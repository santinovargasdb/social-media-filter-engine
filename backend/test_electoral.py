import re

import electoral as el
import pytest


CSV_OK = (
    "consultora,fecha,candidato,porcentaje\n"
    "Consultora X,2026-08-01,Javier Milei,42.5\n"
    "Consultora X,2026-08-01,Axel Kicillof,38.0\n"
    "Consultora Y,2026-08-15,Javier Milei,40.1\n"
)


def test_parse_csv_happy_path():
    rows, warnings = el.parse_pollster_csv(CSV_OK)
    assert len(rows) == 3
    assert warnings == []
    assert rows[0] == {"consultora": "Consultora X", "fecha": "2026-08-01",
                       "candidato": "Javier Milei", "porcentaje": 42.5}
    assert rows[1]["porcentaje"] == 38.0  # coma decimal normalizada


def test_parse_csv_saltea_filas_invalidas_con_warning():
    csv = ("consultora,fecha,candidato,porcentaje\n"
           "Buena,2026-08-01,Milei,40\n"
           "MalPct,2026-08-01,Milei,no-num\n"
           "MalFecha,ayer,Milei,30\n")
    rows, warnings = el.parse_pollster_csv(csv)
    assert len(rows) == 1 and rows[0]["consultora"] == "Buena"
    assert len(warnings) == 2


def test_parse_pct_comma_decimal():
    assert el._parse_pct("42,5") == 42.5


def test_parse_csv_header_invalido_es_error():
    with pytest.raises(ValueError):
        el.parse_pollster_csv("foo,bar\n1,2\n")


def test_parse_csv_vacio_devuelve_vacio():
    rows, warnings = el.parse_pollster_csv("")
    assert rows == [] and warnings == []


def _analysis_fake(items):
    """Devuelve un run_with_rotation fake que responde un mirror fijo."""
    return lambda prompt: (items, None)


def test_analyze_mapea_por_id_y_sanea_postura(monkeypatch):
    import gemini_client as gc
    posts_by_id = {
        "Post_0": {"text": "Milei la rompe", "network": "twitter"},
        "Post_1": {"text": "Basta de Milei", "network": "instagram"},
    }
    mirror = [
        {"id": "Post_0", "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}],
         "cita": "Milei la rompe", "es_electoral": True},
        {"id": "Post_1", "candidatos": [{"nombre": "Javier Milei", "postura": "INVALIDA", "confianza": 0.8}],
         "cita": "Basta de Milei", "es_electoral": True},
    ]
    monkeypatch.setattr(gc, "run_with_rotation", _analysis_fake(mirror))
    out = el.analyze_posts_electoral(posts_by_id)
    assert out[0]["candidatos"][0]["postura"] == "a_favor"
    # postura inválida se descarta -> candidato sin postura válida queda fuera
    assert out[1]["candidatos"] == []


def test_analyze_upstream_falla_devuelve_none(monkeypatch):
    import gemini_client as gc
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (None, 503))
    assert el.analyze_posts_electoral({"Post_0": {"text": "x", "network": "twitter"}}) is None


def test_analyze_ids_inventados_se_ignoran(monkeypatch):
    import gemini_client as gc
    monkeypatch.setattr(gc, "run_with_rotation",
                        _analysis_fake([{"id": "Post_999", "candidatos": [], "cita": "", "es_electoral": False}]))
    out = el.analyze_posts_electoral({"Post_0": {"text": "x", "network": "twitter"}})
    assert out == []  # el id inventado no matchea ningún post


def test_canonical_key_unifica_acentos_y_may():
    assert el.canonical_key("Sergio Massa") == el.canonical_key("  sergio  massa ")
    assert el.canonical_key("Patricia Bullrich") == el.canonical_key("PATRICIA BULLRICH")


def test_aggregate_neto_y_confianza():
    analysis = [
        {"id": "Post_0", "es_electoral": True, "cita": "a",
         "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}]},
        {"id": "Post_1", "es_electoral": True, "cita": "b",
         "candidatos": [{"nombre": "javier  milei", "postura": "a_favor", "confianza": 0.8}]},
        {"id": "Post_2", "es_electoral": True, "cita": "c",
         "candidatos": [{"nombre": "Javier Milei", "postura": "en_contra", "confianza": 0.7}]},
        {"id": "Post_3", "es_electoral": True, "cita": "d",
         "candidatos": [{"nombre": "Axel Kicillof", "postura": "a_favor", "confianza": 0.9}]},
        {"id": "Post_4", "es_electoral": True, "cita": "e",
         "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.2}]},  # baja conf: descartada
    ]
    candidatos, baja_conf = el.aggregate_net_sentiment(analysis)
    assert baja_conf == 1
    milei = next(c for c in candidatos if el.canonical_key(c["nombre"]) == el.canonical_key("Javier Milei"))
    assert (milei["pos"], milei["neg"], milei["menciones"]) == (2, 1, 3)  # sentimiento se conserva
    kici = next(c for c in candidatos if el.canonical_key(c["nombre"]) == el.canonical_key("Axel Kicillof"))
    assert kici["pos"] == 1
    # pct = share de menciones: Milei 3 de 4 (75%), Kicillof 1 de 4 (25%).
    assert milei["pct"] == 75.0 and kici["pct"] == 25.0
    assert candidatos == sorted(candidatos, key=lambda c: c["pct"], reverse=True)


def test_aggregate_pct_es_share_de_menciones():
    """pct siempre es volumen de menciones, sin importar el sentimiento (así todos
    los candidatos detectados aparecen; antes colapsaba a uno con sentimiento neto)."""
    analysis = [
        {"id": "Post_0", "candidatos": [{"nombre": "A", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
        {"id": "Post_1", "candidatos": [{"nombre": "A", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
        {"id": "Post_2", "candidatos": [{"nombre": "B", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
    ]
    candidatos, _bc = el.aggregate_net_sentiment(analysis)
    a = next(c for c in candidatos if c["nombre"] == "A")
    b = next(c for c in candidatos if c["nombre"] == "B")
    assert a["pct"] == 66.7 and b["pct"] == 33.3  # 2/3 y 1/3, aunque todo sea en_contra


def test_build_evidence_ordena_por_confianza_y_adjunta_post():
    analysis = [
        {"id": "Post_0", "cita": "cita floja",
         "candidatos": [{"nombre": "Milei", "postura": "a_favor", "confianza": 0.6}]},
        {"id": "Post_1", "cita": "cita fuerte",
         "candidatos": [{"nombre": "Milei", "postura": "a_favor", "confianza": 0.95}]},
    ]
    posts_by_id = {
        "Post_0": {"text": "t0", "network": "twitter", "post_url": "u0", "author": "a0", "author_url": "", "date": ""},
        "Post_1": {"text": "t1", "network": "tiktok", "post_url": "u1", "author": "a1", "author_url": "", "date": ""},
    }
    ev = el.build_evidence(analysis, posts_by_id)
    assert ev[0]["cita"] == "cita fuerte"  # mayor confianza primero
    assert ev[0]["post"]["post_url"] == "u1"
    assert ev[0]["candidato"] == "Milei"


def test_compare_calcula_gap_y_promedio():
    candidatos = [{"nombre": "Javier Milei", "pct": 44.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    rows = [
        {"consultora": "X", "fecha": "2026-08-01", "candidato": "milei", "porcentaje": 42.0},
        {"consultora": "X", "fecha": "2026-08-20", "candidato": "Milei", "porcentaje": 40.0},  # más reciente gana
        {"consultora": "Y", "fecha": "2026-08-10", "candidato": "Javier Milei", "porcentaje": 46.0},
    ]
    comp, warnings = el.compare_vs_pollsters(candidatos, rows)
    milei = comp[0]
    consultora_x = next(c for c in milei["consultoras"] if c["consultora"] == "X")
    assert consultora_x["pct"] == 40.0 and consultora_x["gap"] == 4.0  # 44 - 40
    assert milei["promedio_consultoras"] == 43.0  # (40 + 46)/2
    assert milei["gap_promedio"] == 1.0           # 44 - 43


def test_compare_avisa_no_reconciliados():
    candidatos = [{"nombre": "Milei", "pct": 44.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    rows = [{"consultora": "X", "fecha": "2026-08-01", "candidato": "Kicillof", "porcentaje": 30.0}]
    comp, warnings = el.compare_vs_pollsters(candidatos, rows)
    assert comp[0]["consultoras"] == [] and comp[0]["promedio_consultoras"] is None
    assert any("Kicillof" in w for w in warnings)   # candidato del CSV sin par en redes


def test_compare_sin_csv_devuelve_solo_redes():
    candidatos = [{"nombre": "Milei", "pct": 100.0, "pos": 0, "neg": 0, "neu": 0, "menciones": 0}]
    comp, warnings = el.compare_vs_pollsters(candidatos, [])
    assert comp[0]["consultoras"] == [] and warnings == []


def test_run_boca_de_urna_flujo_completo(monkeypatch):
    import normalizer, gemini_client as gc
    posts = [
        {"id": "1", "network": "twitter", "author": "a", "author_url": "", "text": "Milei la rompe",
         "date": "", "post_url": "u1", "relevance_score": 80, "relevance_level": "alta",
         "matched_terms": [], "video_url": None},
        {"id": "2", "network": "instagram", "author": "b", "author_url": "", "text": "Kicillof presidente",
         "date": "", "post_url": "u2", "relevance_score": 70, "relevance_level": "alta",
         "matched_terms": [], "video_url": None},
    ]
    monkeypatch.setattr(normalizer, "fetch_raw_posts", lambda **kw: (posts, False))
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: ([
        {"id": "Post_0", "es_electoral": True, "cita": "Milei la rompe",
         "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}]},
        {"id": "Post_1", "es_electoral": True, "cita": "Kicillof presidente",
         "candidatos": [{"nombre": "Axel Kicillof", "postura": "a_favor", "confianza": 0.9}]},
    ], None))

    csv = ("consultora,fecha,candidato,porcentaje\n"
           "X,2026-08-01,Javier Milei,48\n")
    out = el.run_boca_de_urna(keywords=["elecciones"], networks=["twitter", "instagram"],
                              date=None, country="ar", pollster_csv=csv)
    assert out["meta"]["total_posts"] == 2 and out["meta"]["posts_electorales"] == 2
    assert out["meta"]["disclaimer"] == el.DISCLAIMER
    assert {c["nombre"] for c in out["candidatos"]} == {"Javier Milei", "Axel Kicillof"}
    assert len(out["evidencia"]) == 2
    milei_comp = next(c for c in out["comparacion"] if el.canonical_key(c["candidato"]) == el.canonical_key("Javier Milei"))
    assert milei_comp["consultoras"][0]["consultora"] == "X"


def test_run_boca_de_urna_cero_posts(monkeypatch):
    import normalizer
    monkeypatch.setattr(normalizer, "fetch_raw_posts", lambda **kw: ([], False))
    out = el.run_boca_de_urna(keywords=["x"], networks=["twitter"], date=None, country="ar", pollster_csv="")
    assert out["candidatos"] == [] and out["comparacion"] == []
    assert any("publicaciones" in w.lower() for w in out["meta"]["warnings"])


def test_empty_key_no_absorbe_candidatos_csv(monkeypatch):
    """Regresión: un candidato cuyo nombre normaliza a '' (ej. solo espacios o
    caracteres combinatorios puros) no debe matchear ningún CSV ni contaminar
    compare_vs_pollsters; además analyze_posts_electoral debe descartarlo en origen."""
    import gemini_client as gc

    # Nombre que canonical_key reduce a "" (solo espacios).
    NOMBRE_VACIO_KEY = "   "
    assert el.canonical_key(NOMBRE_VACIO_KEY) == ""

    # 1) _keys_match: clave vacía nunca matchea una no vacía (y viceversa).
    assert not el._keys_match("", "javier milei")
    assert not el._keys_match("javier milei", "")
    # Dos vacíos sí se igualan entre sí (corner-case trivial, no produce merge real).
    assert el._keys_match("", "")

    # 2) compare_vs_pollsters: un candidato de redes con nombre de clave vacía no
    #    absorbe los datos de consultoras de candidatos reales.
    candidatos_redes = [
        {"nombre": "Javier Milei", "pct": 44.0, "pos": 4, "neg": 0, "neu": 0, "menciones": 4},
        # nombre que canonical_key reduce a ""
        {"nombre": NOMBRE_VACIO_KEY, "pct": 10.0, "pos": 1, "neg": 0, "neu": 0, "menciones": 1},
    ]
    rows = [{"consultora": "Z", "fecha": "2026-08-01", "candidato": "Javier Milei", "porcentaje": 40.0}]
    comp, warnings = el.compare_vs_pollsters(candidatos_redes, rows)
    # El candidato de clave vacía no debe tener consultoras (no absorbió a Milei).
    vacio = next(c for c in comp if el.canonical_key(c["candidato"]) == "")
    assert vacio["consultoras"] == [] and vacio["promedio_consultoras"] is None
    # Milei sí debe tener su consultora intacta.
    milei = next(c for c in comp if el.canonical_key(c["candidato"]) == "javier milei")
    assert milei["consultoras"][0]["consultora"] == "Z"

    # 3) analyze_posts_electoral: un candidato cuya clave normalizada es '' se descarta.
    posts_by_id = {"Post_0": {"text": "texto irrelevante", "network": "twitter"}}
    monkeypatch.setattr(gc, "run_with_rotation", _analysis_fake([
        {"id": "Post_0", "es_electoral": True, "cita": "",
         "candidatos": [{"nombre": NOMBRE_VACIO_KEY, "postura": "a_favor", "confianza": 0.9}]},
    ]))
    out = el.analyze_posts_electoral(posts_by_id)
    assert out[0]["candidatos"] == []


def test_run_boca_de_urna_upstream_falla_propaga(monkeypatch):
    import normalizer, gemini_client as gc
    posts = [{"id": "1", "network": "twitter", "author": "", "author_url": "", "text": "x", "date": "",
              "post_url": "u", "relevance_score": 50, "relevance_level": "media", "matched_terms": [], "video_url": None}]
    monkeypatch.setattr(normalizer, "fetch_raw_posts", lambda **kw: (posts, False))
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (None, 503))
    with pytest.raises(el.UpstreamUnavailableError):
        el.run_boca_de_urna(keywords=["x"], networks=["twitter"], date=None, country="ar", pollster_csv="")


def test_run_boca_de_urna_serpapi_caido_es_upstream(monkeypatch):
    """Sin posts Y con error de SerpAPI (any_upstream=True) -> 503, no vacío."""
    import normalizer
    monkeypatch.setattr(normalizer, "fetch_raw_posts", lambda **kw: ([], True))
    with pytest.raises(el.UpstreamUnavailableError):
        el.run_boca_de_urna(keywords=["x"], networks=["twitter"], date=None, country="ar", pollster_csv="")


def test_build_candidate_search_list_une_fijos_y_csv():
    # "Maximiliano Pullaro" NO está en la lista fija -> debe sumarse desde el CSV.
    NUEVO_CSV = "Maximiliano Pullaro"
    assert not any(el.canonical_key(NUEVO_CSV) == el.canonical_key(n) for n in el.CANDIDATOS_DEFAULT)
    rows = [
        {"consultora": "X", "fecha": "2026-08-01", "candidato": "Javier Milei", "porcentaje": 42.0},  # ya en la fija
        {"consultora": "X", "fecha": "2026-08-01", "candidato": NUEVO_CSV, "porcentaje": 5.0},        # nuevo
    ]
    lista = el.build_candidate_search_list(rows)
    # Todos los fijos están.
    for fijo in el.CANDIDATOS_DEFAULT:
        assert any(el.canonical_key(fijo) == el.canonical_key(n) for n in lista)
    # El del CSV que no estaba se suma.
    assert any(el.canonical_key(n) == el.canonical_key(NUEVO_CSV) for n in lista)
    # Milei no se duplica (estaba en la fija y en el CSV).
    milei_keys = [n for n in lista if el.canonical_key(n) == el.canonical_key("Javier Milei")]
    assert len(milei_keys) == 1


def test_run_boca_de_urna_busca_por_candidato(monkeypatch):
    """El corpus se arma con la búsqueda general (todas las redes) + una por cada
    candidato fijo. Las de candidato van SOLO a X para ahorrar cuota de SerpAPI."""
    import normalizer
    llamados = []  # (termino, tuple(redes))

    def fake_fetch(**kw):
        llamados.append((kw["termino"], tuple(kw["networks"])))
        return [], False

    monkeypatch.setattr(normalizer, "fetch_raw_posts", fake_fetch)
    el.run_boca_de_urna(keywords=["elecciones presidenciales"],
                        networks=["twitter", "instagram", "tiktok"],
                        date=None, country="ar", pollster_csv="")
    # La búsqueda general usa TODAS las redes seleccionadas.
    assert ("elecciones presidenciales", ("twitter", "instagram", "tiktok")) in llamados
    # Cada búsqueda por candidato usa solo X.
    por_candidato = [c for c in llamados if c[0] != "elecciones presidenciales"]
    assert por_candidato and all(nets == ("twitter",) for _term, nets in por_candidato)
    assert any("Javier Milei" in term for term, _ in por_candidato)
    # general + una por cada candidato fijo (deduplicado por texto).
    assert len(llamados) == 1 + len(el.CANDIDATOS_DEFAULT)


def _fake_rotation_por_contenido(monkeypatch, fail_if_contains_index=None):
    """Instala un run_with_rotation que responde según el CONTENIDO del lote (los
    lotes corren en paralelo, así que no se puede depender del orden de llamada).

    Cada post de test tiene texto 'MARK_<i>'; el fake detecta qué índices trae el
    prompt y devuelve un mirror con esos ids. Si `fail_if_contains_index` está en
    el lote, ese lote devuelve (None, 503) simulando una falla upstream. Registra
    las llamadas en la lista devuelta (append es thread-safe bajo el GIL)."""
    import gemini_client as gc
    calls = []

    def fake(prompt):
        calls.append(1)
        idx = [int(m) for m in re.findall(r"MARK_(\d+)", prompt)]
        if fail_if_contains_index is not None and fail_if_contains_index in idx:
            return None, 503
        mirror = [{"id": f"Post_{i}", "candidatos": [], "cita": "", "es_electoral": True} for i in idx]
        return mirror, None

    monkeypatch.setattr(gc, "run_with_rotation", fake)
    return calls


def test_analyze_posts_electoral_batchea_y_mergea(monkeypatch):
    """Con más posts que ELECTORAL_BATCH_SIZE se hacen varias llamadas (en paralelo)
    y cada lote conserva SOLO sus propios ids; el merge no pierde ni duplica."""
    n = el.ELECTORAL_BATCH_SIZE + 5
    posts_by_id = {f"Post_{i}": {"text": f"MARK_{i}", "network": "twitter"} for i in range(n)}
    calls = _fake_rotation_por_contenido(monkeypatch)
    out = el.analyze_posts_electoral(posts_by_id)
    assert len(calls) == 2                # ceil(n / BATCH_SIZE) == 2 lotes
    assert len(out) == n                  # cada id aparece exactamente una vez
    assert {o["id"] for o in out} == set(posts_by_id)


def test_analyze_posts_electoral_fallo_parcial_devuelve_parcial(monkeypatch):
    """Si un lote falla (None) pero otro anda, se devuelven resultados parciales,
    no None. Independiente del orden en que terminen los lotes paralelos."""
    n = el.ELECTORAL_BATCH_SIZE + 5
    posts_by_id = {f"Post_{i}": {"text": f"MARK_{i}", "network": "twitter"} for i in range(n)}
    # El lote que contiene Post_0 (el primero, de tamaño BATCH_SIZE) falla.
    calls = _fake_rotation_por_contenido(monkeypatch, fail_if_contains_index=0)
    out = el.analyze_posts_electoral(posts_by_id)
    assert out is not None
    assert len(calls) == 2
    # Sobreviven todos menos el primer lote (Post_0..Post_{BATCH_SIZE-1}).
    assert len(out) == n - el.ELECTORAL_BATCH_SIZE
    assert all(int(o["id"].split("_")[1]) >= el.ELECTORAL_BATCH_SIZE for o in out)
