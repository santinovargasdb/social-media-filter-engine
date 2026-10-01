"""Tests del modo 'stored' de run_boca_de_urna — la fecha elige el snapshot del día."""
import pytest

import electoral
import store


def _snap(origen):
    return {"candidatos": [], "evidencia": [], "comparacion": [],
            "meta": {"warnings": [], "origen": origen}}


def _run(date=None):
    return electoral.run_boca_de_urna(keywords=[], networks=["twitter"], date=date,
                                      country="ar", pollster_csv="")


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
