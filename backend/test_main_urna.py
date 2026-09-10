"""Tests del endpoint /api/boca-de-urna: invocan el coroutine directo (sin httpx)."""
import asyncio
import pytest
from fastapi import HTTPException
import main
import electoral


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
