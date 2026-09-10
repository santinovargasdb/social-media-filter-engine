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
    candidatos, baja_conf, fallback = el.aggregate_net_sentiment(analysis)
    assert baja_conf == 1 and fallback is False
    milei = next(c for c in candidatos if el.canonical_key(c["nombre"]) == el.canonical_key("Javier Milei"))
    assert (milei["pos"], milei["neg"], milei["menciones"]) == (2, 1, 3)  # net = 1
    kici = next(c for c in candidatos if el.canonical_key(c["nombre"]) == el.canonical_key("Axel Kicillof"))
    assert kici["pos"] == 1  # net = 1
    # net Milei = 1, net Kicillof = 1 -> 50/50
    assert milei["pct"] == 50.0 and kici["pct"] == 50.0
    assert candidatos == sorted(candidatos, key=lambda c: c["pct"], reverse=True)


def test_aggregate_fallback_a_volumen_si_todos_negativos():
    analysis = [
        {"id": "Post_0", "candidatos": [{"nombre": "A", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
        {"id": "Post_1", "candidatos": [{"nombre": "A", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
        {"id": "Post_2", "candidatos": [{"nombre": "B", "postura": "en_contra", "confianza": 0.9}], "cita": ""},
    ]
    candidatos, _bc, fallback = el.aggregate_net_sentiment(analysis)
    assert fallback is True
    a = next(c for c in candidatos if c["nombre"] == "A")
    assert a["pct"] == 66.7  # 2 de 3 menciones


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
