"""Tests del almacén en memoria de trabajos async (jobs.py). No usa red ni tiempo real
para el TTL (se manipula el timestamp guardado)."""
import time
import jobs


def test_create_devuelve_id_unico_y_estado_running():
    jobs._JOBS.clear()
    a = jobs.create()
    b = jobs.create()
    assert a != b
    j = jobs.get(a)
    assert j["state"] == "running" and j["result"] is None and j["error"] is None


def test_set_progress_se_refleja_en_get():
    jobs._JOBS.clear()
    jid = jobs.create()
    jobs.set_progress(jid, "Buscando…", 40)
    j = jobs.get(jid)
    assert j["progress"] == {"phase": "Buscando…", "pct": 40.0}


def test_set_result_pasa_a_done():
    jobs._JOBS.clear()
    jid = jobs.create()
    jobs.set_result(jid, {"candidatos": [], "meta": {}})
    j = jobs.get(jid)
    assert j["state"] == "done" and j["result"] == {"candidatos": [], "meta": {}}
    assert j["progress"]["pct"] == 100.0


def test_set_error_pasa_a_error():
    jobs._JOBS.clear()
    jid = jobs.create()
    jobs.set_error(jid, "Gemini caído")
    j = jobs.get(jid)
    assert j["state"] == "error" and j["error"] == "Gemini caído"


def test_get_desconocido_devuelve_none():
    jobs._JOBS.clear()
    assert jobs.get("no-existe") is None


def test_get_devuelve_copia_no_alias():
    """get() no debe exponer el dict interno mutable (evita corromper el almacén)."""
    jobs._JOBS.clear()
    jid = jobs.create()
    j = jobs.get(jid)
    j["state"] = "hackeado"
    assert jobs.get(jid)["state"] == "running"


def test_ttl_barre_trabajos_viejos_al_crear():
    jobs._JOBS.clear()
    viejo = jobs.create()
    # Forzamos su timestamp a más allá del TTL.
    jobs._JOBS[viejo]["updated"] = time.time() - (jobs._TTL + 60)
    nuevo = jobs.create()  # create() barre los vencidos
    assert jobs.get(viejo) is None
    assert jobs.get(nuevo) is not None
