"""Tests del modo 'stored' de run_boca_de_urna — la fecha elige el snapshot del día
y las consultoras (CSV / automáticas) se calculan ENCIMA del snapshot."""
import pytest

import electoral
import pollsters
import store


def _snap(origen, candidatos=None, comparacion=None):
    return {"candidatos": candidatos or [], "evidencia": [],
            "comparacion": comparacion if comparacion is not None else [],
            "meta": {"warnings": [], "origen": origen}}


def _run(date=None, pollster_csv="", auto_consultoras=False):
    return electoral.run_boca_de_urna(keywords=[], networks=["twitter"], date=date,
                                      country="ar", pollster_csv=pollster_csv,
                                      auto_consultoras=auto_consultoras)


def test_stored_sin_fecha_sirve_el_ultimo(monkeypatch):
    monkeypatch.setenv("URNA_FETCH_BACKEND", "stored")
    monkeypatch.setattr(store, "read_latest_snapshot", lambda: _snap("último"))
    assert _run()["meta"]["origen"] == "último"


def test_stored_con_fecha_sirve_el_de_ese_dia(monkeypatch):
    monkeypatch.setenv("URNA_FETCH_BACKEND", "stored")
    fechas = []
    monkeypatch.setattr(store, "read_snapshot_for_date",
                        lambda fecha: fechas.append(fecha) or _snap("del día"))
    monkeypatch.setattr(store, "read_latest_snapshot",
                        lambda: pytest.fail("con fecha NO debe caer al último"))
    assert _run(date="2026-09-29")["meta"]["origen"] == "del día"
    assert fechas == ["2026-09-29"]


def test_stored_con_fecha_sin_snapshot_explica_con_la_fecha(monkeypatch):
    monkeypatch.setenv("URNA_FETCH_BACKEND", "stored")
    monkeypatch.setattr(store, "read_snapshot_for_date", lambda fecha: None)
    with pytest.raises(electoral.UpstreamUnavailableError) as exc:
        _run(date="2026-09-20")
    assert "2026-09-20" in str(exc.value)


MILEI = {"nombre": "Javier Milei", "pct": 40.0, "menciones": 24,
         "pos": 10, "neg": 8, "neu": 6, "pos_pct": 42, "neg_pct": 33, "neu_pct": 25}

CSV = "consultora,fecha,candidato,porcentaje\nAnalía,2026-09-20,Javier Milei,38"


def test_stored_con_csv_recalcula_comparacion(monkeypatch):
    """El snapshot sube la comparación VACÍA (el scraper no conoce consultoras):
    con CSV, la comparación se calcula acá sobre los candidatos guardados."""
    monkeypatch.setenv("URNA_FETCH_BACKEND", "stored")
    monkeypatch.setattr(store, "read_latest_snapshot", lambda: _snap("x", [dict(MILEI)]))
    res = _run(pollster_csv=CSV)
    filas = {c["consultora"] for comp in res["comparacion"] for c in comp["consultoras"]}
    assert "Analía" in filas


def test_stored_con_auto_consultoras_busca_y_compara(monkeypatch):
    monkeypatch.setenv("URNA_FETCH_BACKEND", "stored")
    monkeypatch.setattr(store, "read_latest_snapshot", lambda: _snap("x", [dict(MILEI)]))
    pedidos = []
    monkeypatch.setattr(pollsters, "fetch_pollster_rows",
                        lambda fecha_desde=None, country="ar":
                        (pedidos.append((fecha_desde, country)) or
                         ([{"consultora": "AutoPoll", "fecha": "2026-09-20",
                            "candidato": "Javier Milei", "porcentaje": 35.0}], ["warn auto"])))
    res = _run(auto_consultoras=True)
    assert pedidos == [(None, "ar")]
    filas = {c["consultora"] for comp in res["comparacion"] for c in comp["consultoras"]}
    assert "AutoPoll" in filas
    assert "warn auto" in res["meta"]["warnings"]


def test_stored_sin_consultoras_no_toca_la_comparacion_del_snapshot(monkeypatch):
    monkeypatch.setenv("URNA_FETCH_BACKEND", "stored")
    centinela = [{"candidato": "tal cual vino"}]
    monkeypatch.setattr(store, "read_latest_snapshot",
                        lambda: _snap("x", [dict(MILEI)], comparacion=centinela))
    assert _run()["comparacion"] == centinela
