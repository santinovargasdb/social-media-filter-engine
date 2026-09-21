"""
Tests del almacén de snapshots (store.py, Supabase). No pegan a la red: se mockea
la llamada HTTP. Las respuestas imitan la forma de PostgREST de Supabase.
"""
import store


class _Resp:
    def __init__(self, data, code=200):
        self._data = data
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise store.requests.exceptions.HTTPError(str(self.status_code))

    def json(self):
        return self._data


def test_read_latest_snapshot_parsea_e_inyecta_la_fecha(monkeypatch):
    monkeypatch.setattr(store, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(store, "SUPABASE_KEY", "k")
    row = {"payload": {"candidatos": [{"nombre": "Javier Milei"}], "evidencia": [],
                       "comparacion": [], "meta": {"total_posts": 5, "warnings": []}},
           "generado_en": "2026-09-21T10:00:00Z"}
    monkeypatch.setattr(store.requests, "get", lambda url, **kw: _Resp([row]))
    snap = store.read_latest_snapshot()
    assert snap["candidatos"][0]["nombre"] == "Javier Milei"
    # la fecha de generación se inyecta en meta para el "última actualización"
    assert snap["meta"]["ultima_actualizacion"] == "2026-09-21T10:00:00Z"


def test_read_latest_snapshot_sin_config_devuelve_none(monkeypatch):
    monkeypatch.setattr(store, "SUPABASE_URL", "")
    monkeypatch.setattr(store, "SUPABASE_KEY", "")
    assert store.read_latest_snapshot() is None


def test_read_latest_snapshot_tabla_vacia_devuelve_none(monkeypatch):
    monkeypatch.setattr(store, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(store, "SUPABASE_KEY", "k")
    monkeypatch.setattr(store.requests, "get", lambda url, **kw: _Resp([]))
    assert store.read_latest_snapshot() is None


def test_read_latest_snapshot_error_de_red_devuelve_none(monkeypatch):
    monkeypatch.setattr(store, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(store, "SUPABASE_KEY", "k")

    def boom(url, **kw):
        raise store.requests.exceptions.ConnectionError("caído")

    monkeypatch.setattr(store.requests, "get", boom)
    assert store.read_latest_snapshot() is None


def test_write_snapshot_postea_y_devuelve_true(monkeypatch):
    monkeypatch.setattr(store, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(store, "SUPABASE_KEY", "k")
    enviado = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        enviado["url"] = url
        enviado["json"] = json
        return _Resp({}, 201)

    monkeypatch.setattr(store.requests, "post", fake_post)
    ok = store.write_snapshot({"candidatos": []}, "2026-09-21T10:00:00Z")
    assert ok is True
    assert enviado["json"]["generado_en"] == "2026-09-21T10:00:00Z"
    assert enviado["json"]["payload"] == {"candidatos": []}


def test_write_snapshot_sin_config_devuelve_false(monkeypatch):
    monkeypatch.setattr(store, "SUPABASE_URL", "")
    monkeypatch.setattr(store, "SUPABASE_KEY", "")
    assert store.write_snapshot({}, "2026-09-21T10:00:00Z") is False
