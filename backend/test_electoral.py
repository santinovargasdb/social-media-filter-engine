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
