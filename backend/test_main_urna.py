"""Tests del endpoint /api/boca-de-urna: invocan el coroutine directo (sin httpx)."""
import asyncio
import time
import pytest
from fastapi import HTTPException
import main
import electoral


def _wait_done(job_id, timeout=3.0):
    """Espera a que el trabajo async deje de estar 'running' (el hilo termina rápido
    con run_boca_de_urna mockeado)."""
    deadline = time.time() + timeout
    st = asyncio.run(main.boca_de_urna_status(job_id))
    while st["state"] == "running" and time.time() < deadline:
        time.sleep(0.02)
        st = asyncio.run(main.boca_de_urna_status(job_id))
    return st


def _req(**over):
    base = {"keywords": ["elecciones"], "networks": ["twitter"], "date": None,
            "country": "ar", "pollster_csv": ""}
    base.update(over)
    return main.BocaDeUrnaRequest(**base)


def test_endpoint_ok(monkeypatch):
    payload_out = {"candidatos": [{"nombre": "Milei", "pct": 55.0, "pos": 5, "neg": 1, "neu": 0, "menciones": 6}],
                   "evidencia": [], "comparacion": [],
                   "meta": {"total_posts": 6, "posts_electorales": 6, "disclaimer": "x", "warnings": []}}
    monkeypatch.setattr(electoral, "run_boca_de_urna", lambda **kw: payload_out)
    out = asyncio.run(main.boca_de_urna_endpoint(_req()))
    assert out["candidatos"][0]["nombre"] == "Milei"


def test_endpoint_csv_invalido_es_400(monkeypatch):
    def boom(**kw):
        raise ValueError("CSV inválido: faltan columnas ['porcentaje'].")
    monkeypatch.setattr(electoral, "run_boca_de_urna", boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.boca_de_urna_endpoint(_req(pollster_csv="malo")))
    assert ei.value.status_code == 400 and "CSV" in ei.value.detail


def test_endpoint_upstream_es_503(monkeypatch):
    def boom(**kw):
        raise electoral.UpstreamUnavailableError("Gemini caído")
    monkeypatch.setattr(electoral, "run_boca_de_urna", boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.boca_de_urna_endpoint(_req()))
    assert ei.value.status_code == 503


def test_endpoint_pasa_auto_consultoras(monkeypatch):
    capturado = {}

    def fake_run(**kw):
        capturado.update(kw)
        return {"candidatos": [], "evidencia": [], "comparacion": [],
                "meta": {"total_posts": 0, "posts_electorales": 0, "disclaimer": "x", "warnings": []}}

    monkeypatch.setattr(electoral, "run_boca_de_urna", fake_run)
    asyncio.run(main.boca_de_urna_endpoint(_req(auto_consultoras=True)))
    assert capturado["auto_consultoras"] is True


# ── Flujo ASÍNCRONO: /start + /status/{job_id} ────────────────────────────────
def test_start_devuelve_job_id_y_status_llega_a_done(monkeypatch):
    payload_out = {"candidatos": [{"nombre": "Milei", "pct": 55.0, "pos": 5, "neg": 1, "neu": 0,
                                   "menciones": 6, "pos_pct": 83.3, "neg_pct": 16.7, "neu_pct": 0.0}],
                   "evidencia": [], "comparacion": [],
                   "meta": {"total_posts": 6, "posts_electorales": 6, "disclaimer": "x", "warnings": []}}
    monkeypatch.setattr(electoral, "run_boca_de_urna", lambda **kw: payload_out)
    r = asyncio.run(main.boca_de_urna_start(_req()))
    assert "job_id" in r
    st = _wait_done(r["job_id"])
    assert st["state"] == "done"
    assert st["result"]["candidatos"][0]["nombre"] == "Milei"


def test_status_desconocido_es_404():
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.boca_de_urna_status("no-existe"))
    assert ei.value.status_code == 404


def test_start_error_queda_en_estado_error(monkeypatch):
    def boom(**kw):
        raise electoral.UpstreamUnavailableError("Gemini caído")
    monkeypatch.setattr(electoral, "run_boca_de_urna", boom)
    r = asyncio.run(main.boca_de_urna_start(_req()))
    st = _wait_done(r["job_id"])
    assert st["state"] == "error" and "Gemini" in st["error"]


def test_start_error_generico_no_filtra_el_mensaje_interno(monkeypatch):
    """Un error inesperado (no ValueError/Upstream) NO debe filtrar str(e) crudo al
    usuario (paths, tokens, stacktrace). Se muestra un mensaje genérico; el detalle
    queda en los logs del servidor."""
    SECRETO = "KeyError: '/ruta/interna' token=abc123secreto"

    def boom(**kw):
        raise RuntimeError(SECRETO)

    monkeypatch.setattr(electoral, "run_boca_de_urna", boom)
    r = asyncio.run(main.boca_de_urna_start(_req()))
    st = _wait_done(r["job_id"])
    assert st["state"] == "error"
    assert SECRETO not in (st["error"] or "")        # no filtra el interno
    assert "interno" in (st["error"] or "").lower()  # mensaje genérico


def test_start_pasa_progress_cb_callable(monkeypatch):
    capturado = {}

    def fake_run(progress_cb=None, **kw):
        capturado["cb_callable"] = callable(progress_cb)
        progress_cb("Analizando…", 40)  # no debe romper
        return {"candidatos": [], "evidencia": [], "comparacion": [],
                "meta": {"total_posts": 0, "posts_electorales": 0, "disclaimer": "x", "warnings": []}}

    monkeypatch.setattr(electoral, "run_boca_de_urna", fake_run)
    r = asyncio.run(main.boca_de_urna_start(_req()))
    _wait_done(r["job_id"])
    assert capturado["cb_callable"] is True
