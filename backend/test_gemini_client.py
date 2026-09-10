"""Smoke test del transporte extraído: cascada y rotación, con Gemini mockeado."""
import gemini_client as gc


def test_run_cascade_primer_modelo_ok(monkeypatch):
    llamadas = []
    def fake(model, api_key, prompt):
        llamadas.append(model)
        return ([{"id": "Post_0"}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, status = gc.run_cascade("p", "k")
    assert parsed == [{"id": "Post_0"}]
    assert len(llamadas) == 1  # no probó fallback


def test_run_cascade_fallback_en_429(monkeypatch):
    def fake(model, api_key, prompt):
        if model == gc.GEMINI_MODELS[0]:
            return (None, 429)
        return ([{"ok": True}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, _ = gc.run_cascade("p", "k")
    assert parsed == [{"ok": True}]


def test_run_cascade_error_no_reintentable_corta(monkeypatch):
    llamadas = []
    def fake(model, api_key, prompt):
        llamadas.append(model)
        return (None, 400)  # no está en GEMINI_RETRY_STATUSES
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, status = gc.run_cascade("p", "k")
    assert parsed is None and status == 400
    assert len(llamadas) == 1


def test_run_with_rotation_sin_key_devuelve_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gc.run_with_rotation("p") == (None, None)


def test_run_with_rotation_rota_a_secundaria_en_429(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "primary")
    monkeypatch.setenv("GEMINI_API_KEY_SECONDARY", "secondary")
    keys = []
    def fake_cascade(prompt, api_key):
        keys.append(api_key)
        return (None, 429) if api_key == "primary" else ([{"ok": True}], None)
    monkeypatch.setattr(gc, "run_cascade", fake_cascade)
    parsed, _ = gc.run_with_rotation("p")
    assert parsed == [{"ok": True}]
    assert keys == ["primary", "secondary"]
