"""Smoke test del transporte extraído: cascada y rotación, con Gemini mockeado."""
import gemini_client as gc


def test_run_cascade_primer_modelo_ok(monkeypatch):
    llamadas = []
    def fake(model, api_key, prompt, image=None):
        llamadas.append(model)
        return ([{"id": "Post_0"}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, status = gc.run_cascade("p", "k")
    assert parsed == [{"id": "Post_0"}]
    assert len(llamadas) == 1  # no probó fallback


def test_run_cascade_fallback_en_429(monkeypatch):
    def fake(model, api_key, prompt, image=None):
        if model == gc.GEMINI_MODELS[0]:
            return (None, 429)
        return ([{"ok": True}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, _ = gc.run_cascade("p", "k")
    assert parsed == [{"ok": True}]


def test_run_cascade_error_de_red_prueba_siguiente(monkeypatch):
    """Timeout/parseo (status None) no aborta la cascada: el siguiente modelo
    puede responder (medido 2026-09-23: 503 en 2.5-flash + read-timeout en
    2.5-flash-lite dejaban sin probar los 3.x)."""
    def fake(model, api_key, prompt, image=None):
        if model == gc.GEMINI_MODELS[0]:
            return (None, 503)
        if model == gc.GEMINI_MODELS[1]:
            return (None, None)  # error de red (read timeout)
        return ([{"ok": True}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, _ = gc.run_cascade("p", "k")
    assert parsed == [{"ok": True}]


def test_run_cascade_error_no_reintentable_corta(monkeypatch):
    llamadas = []
    def fake(model, api_key, prompt, image=None):
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
    def fake_cascade(prompt, api_key, image=None):
        keys.append(api_key)
        return (None, 429) if api_key == "primary" else ([{"ok": True}], None)
    monkeypatch.setattr(gc, "run_cascade", fake_cascade)
    parsed, _ = gc.run_with_rotation("p")
    assert parsed == [{"ok": True}]
    assert keys == ["primary", "secondary"]


class _FakeResp:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}


def test_generate_raw_texto_no_manda_inline_data(monkeypatch):
    """Sin imagen, el payload es EXACTAMENTE el de siempre (una sola part de texto)."""
    capturado = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        capturado["payload"] = json
        return _FakeResp()
    monkeypatch.setattr(gc.requests, "post", fake_post)
    raw, status = gc.generate_raw("m", "k", "hola")
    assert raw == "[]" and status is None
    assert capturado["payload"]["contents"][0]["parts"] == [{"text": "hola"}]


def test_generate_raw_vision_manda_inline_data(monkeypatch):
    """Con imagen, el payload lleva la part de texto + inline_data con mime y base64."""
    capturado = {}
    def fake_post(url, headers=None, json=None, timeout=None):
        capturado["payload"] = json
        return _FakeResp()
    monkeypatch.setattr(gc.requests, "post", fake_post)
    raw, _ = gc.generate_raw("m", "k", "lee la captura", image=("QUJD", "image/png"))
    assert raw == "[]"
    parts = capturado["payload"]["contents"][0]["parts"]
    assert parts[0] == {"text": "lee la captura"}
    assert parts[1] == {"inline_data": {"mime_type": "image/png", "data": "QUJD"}}


def test_cascada_y_rotacion_pasan_la_imagen(monkeypatch):
    """run_with_rotation → run_cascade → call_gemini_json propagan la imagen intacta."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GEMINI_API_KEY_SECONDARY", raising=False)
    imagenes = []
    def fake(model, api_key, prompt, image=None):
        imagenes.append(image)
        return ([{"ok": True}], None)
    monkeypatch.setattr(gc, "call_gemini_json", fake)
    parsed, _ = gc.run_with_rotation("p", image=("DATA", "image/webp"))
    assert parsed == [{"ok": True}]
    assert imagenes == [("DATA", "image/webp")]
