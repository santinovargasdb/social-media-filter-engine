"""Tests de store.py — snapshot por fecha e historial (requests mockeado, sin red)."""
import pytest

import store


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def _mock_get(monkeypatch, rows, capturadas):
    def fake_get(url, params=None, headers=None, timeout=None):
        capturadas.append((url, params))
        return _Resp(rows)
    monkeypatch.setattr(store.requests, "get", fake_get)
    monkeypatch.setattr(store, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(store, "SUPABASE_KEY", "k")


def test_read_snapshot_for_date_consulta_hasta_fin_del_dia_argentino(monkeypatch):
    capturadas = []
    _mock_get(monkeypatch, [{"payload": {"meta": {"warnings": []}},
                             "generado_en": "2026-09-29T19:00:04+00:00"}], capturadas)
    snap = store.read_snapshot_for_date("2026-09-29")
    _, params = capturadas[0]
    # El día argentino (UTC-3) del 29/9 termina a las 03:00 UTC del 30/9.
    assert params["generado_en"] == "lt.2026-09-30T03:00:00+00:00"
    assert params["order"] == "generado_en.desc" and params["limit"] == 1
    assert snap["meta"]["ultima_actualizacion"] == "2026-09-29T19:00:04+00:00"
    assert snap["meta"]["warnings"] == []  # el snapshot ES de ese día: sin aviso extra


def test_read_snapshot_for_date_dia_sin_corrida_avisa_primero(monkeypatch):
    # El 29/9 no hubo corrida: devuelve el más cercano ANTERIOR, avisando PRIMERO
    # (el snapshot puede arrastrar decenas de warnings históricos propios y el
    # aviso de fecha no debe quedar enterrado al final).
    _mock_get(monkeypatch, [{"payload": {"meta": {"warnings": ["histórico viejo"]}},
                             "generado_en": "2026-09-27T19:00:00+00:00"}], [])
    snap = store.read_snapshot_for_date("2026-09-29")
    assert "anterior" in snap["meta"]["warnings"][0]
    assert snap["meta"]["warnings"][1] == "histórico viejo"


def test_read_snapshot_for_date_sin_filas_devuelve_none(monkeypatch):
    _mock_get(monkeypatch, [], [])
    assert store.read_snapshot_for_date("2026-09-29") is None


def test_read_snapshot_for_date_fecha_invalida_lanza_valueerror(monkeypatch):
    _mock_get(monkeypatch, [], [])
    with pytest.raises(ValueError):
        store.read_snapshot_for_date("29/09/2026")


def test_read_snapshot_history_liviano_y_ascendente(monkeypatch):
    capturadas = []
    rows = [
        {"generado_en": "2026-10-01T12:33:36+00:00",
         "candidatos": [{"nombre": "Javier Milei", "menciones": 24, "pct": 25.3}]},
        {"generado_en": "2026-09-29T19:00:04+00:00",
         "candidatos": [{"nombre": "Javier Milei", "menciones": 10, "pct": 23.8}]},
    ]
    _mock_get(monkeypatch, rows, capturadas)
    serie = store.read_snapshot_history()
    _, params = capturadas[0]
    # Solo generado_en + candidatos (el payload entero con evidencia sería pesadísimo).
    assert params["select"] == "generado_en,candidatos:payload->candidatos"
    # desc + tope en la query, reversa en Python: con más filas que el tope
    # sobreviven las MÁS NUEVAS, pero la serie sale ascendente para graficar.
    assert params["order"] == "generado_en.desc"
    assert [p["generado_en"] for p in serie] == [
        "2026-09-29T19:00:04+00:00", "2026-10-01T12:33:36+00:00"]


def test_read_snapshot_history_sin_config_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(store, "SUPABASE_URL", "")
    assert store.read_snapshot_history() == []
