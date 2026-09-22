"""Unit tests de vision.py — Gemini SIEMPRE mockeado (sin red, sin API key)."""
import base64
import json

import vision


PARSED_OK = [
    {"autor": "@ok", "fecha": "2 h", "texto": "Gran discurso de Milei",
     "es_electoral": True,
     "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 1.7}],
     "cita": "Gran discurso"},
    {"autor": "@vacio", "fecha": "", "texto": "   ",
     "es_electoral": False, "candidatos": [], "cita": ""},
    {"autor": "@raro", "fecha": "1 d", "texto": "posturas inválidas",
     "es_electoral": True,
     "candidatos": [{"nombre": "Javier Milei", "postura": "me_gusta", "confianza": 0.9},
                    {"nombre": "", "postura": "a_favor", "confianza": 0.9},
                    {"nombre": "Axel Kicillof", "postura": "EN_CONTRA", "confianza": "alta"}],
     "cita": ""},
]


def _mock_gemini(monkeypatch, respuesta=(PARSED_OK, None)):
    visto = {}
    def fake(prompt, image=None):
        visto["prompt"] = prompt
        visto["image"] = image
        return respuesta
    monkeypatch.setattr(vision.gemini_client, "run_with_rotation", fake)
    return visto


def test_read_capture_sanea_y_completa(monkeypatch, tmp_path):
    """Camino feliz: manda la imagen en base64, sanea posturas/confianza y agrega la red."""
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"fake-png")
    visto = _mock_gemini(monkeypatch)

    posts = vision.read_capture(cap, "twitter")

    assert visto["image"] == (base64.b64encode(b"fake-png").decode("ascii"), "image/png")
    # El post sin texto se descarta; quedan 2.
    assert [p["texto"] for p in posts] == ["Gran discurso de Milei", "posturas inválidas"]
    p0 = posts[0]
    assert p0["red"] == "twitter" and p0["autor"] == "@ok" and p0["fecha"] == "2 h"
    assert p0["es_electoral"] is True and p0["cita"] == "Gran discurso"
    # Confianza 1.7 clampeada a 1.0.
    assert p0["candidatos"] == [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 1.0}]
    # Postura inválida y nombre vacío descartados; "EN_CONTRA" normaliza; "alta" -> 0.0.
    assert posts[1]["candidatos"] == [{"nombre": "Axel Kicillof", "postura": "en_contra", "confianza": 0.0}]


def test_read_capture_upstream_devuelve_none(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    _mock_gemini(monkeypatch, respuesta=(None, 503))
    assert vision.read_capture(cap, "twitter") is None


def test_read_capture_acepta_bytes_y_jpg(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    visto = _mock_gemini(monkeypatch, respuesta=([], None))
    assert vision.read_capture(b"crudo", "twitter") == []
    assert visto["image"][1] == "image/png"  # bytes sin mime -> png por default
    cap = tmp_path / "cap.jpg"
    cap.write_bytes(b"jpg")
    vision.read_capture(cap, "twitter")
    assert visto["image"][1] == "image/jpeg"


def test_prompt_incluye_reglas_red_y_candidatos(monkeypatch, tmp_path):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    visto = _mock_gemini(monkeypatch, respuesta=([], None))
    vision.read_capture(cap, "twitter")
    prompt = visto["prompt"]
    # Reglas compartidas con el clasificador de texto (una sola fuente de verdad).
    assert "REGLA DE ENCUESTAS/SONDEOS" in prompt
    assert '"a_favor"' in prompt and '"en_contra"' in prompt and '"neutro"' in prompt
    assert '"twitter"' in prompt
    assert "Javier Milei" in prompt  # CANDIDATOS_DEFAULT como referencia
    assert "Promocionado" in prompt  # regla de ignorar publicidad
    # Con lista propia, usa esa lista.
    vision.read_capture(cap, "twitter", candidatos=["Fulano de Tal"])
    assert "Fulano de Tal" in visto["prompt"]


def test_sanitize_descarta_items_no_dict():
    assert vision._sanitize_posts(["texto suelto", 42, {"texto": "ok", "candidatos": []}], "x") == [
        {"texto": "ok", "autor": "", "fecha": "", "red": "x",
         "es_electoral": False, "candidatos": [], "cita": ""}]


def test_pace_espera_lo_que_falta(monkeypatch):
    """Si pasaron 4s de un intervalo de 10, duerme los 6 restantes."""
    monkeypatch.setenv("VISION_MIN_INTERVAL", "10")
    monkeypatch.setattr(vision, "_last_call", 100.0)
    tiempos = iter([104.0, 110.0])
    dormido = []
    monkeypatch.setattr(vision.time, "monotonic", lambda: next(tiempos))
    monkeypatch.setattr(vision.time, "sleep", lambda s: dormido.append(s))
    vision._pace()
    assert dormido == [6.0]
    assert vision._last_call == 110.0


def test_pace_intervalo_cumplido_no_espera(monkeypatch):
    monkeypatch.setenv("VISION_MIN_INTERVAL", "10")
    monkeypatch.setattr(vision, "_last_call", 100.0)
    monkeypatch.setattr(vision.time, "monotonic", lambda: 250.0)
    monkeypatch.setattr(vision.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("no debía dormir")))
    vision._pace()


def test_pace_cero_desactiva_y_primera_llamada_no_espera(monkeypatch):
    monkeypatch.setattr(vision.time, "sleep",
                        lambda s: (_ for _ in ()).throw(AssertionError("no debía dormir")))
    monkeypatch.setattr(vision.time, "monotonic", lambda: 300.0)
    # VISION_MIN_INTERVAL=0 -> nunca espera, aunque la última llamada sea reciente.
    monkeypatch.setenv("VISION_MIN_INTERVAL", "0")
    monkeypatch.setattr(vision, "_last_call", 299.0)
    vision._pace()
    # Primera llamada (_last_call == 0) -> no espera aunque haya intervalo.
    monkeypatch.setenv("VISION_MIN_INTERVAL", "10")
    monkeypatch.setattr(vision, "_last_call", 0.0)
    vision._pace()


def test_cli_imprime_json(monkeypatch, capsys, tmp_path):
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    monkeypatch.setattr(vision, "read_capture",
                        lambda image, red, candidatos=None, mime=None: [{"texto": "hola", "red": red}])
    rc = vision.main([str(cap), "--red", "twitter"])
    assert rc == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida == [{"texto": "hola", "red": "twitter"}]


def test_cli_pasa_candidatos_y_falla_con_upstream(monkeypatch, capsys, tmp_path):
    cap = tmp_path / "cap.png"
    cap.write_bytes(b"x")
    visto = {}
    def fake_read(image, red, candidatos=None, mime=None):
        visto["candidatos"] = candidatos
        return None
    monkeypatch.setattr(vision, "read_capture", fake_read)
    rc = vision.main([str(cap), "--candidatos", "Juan Pérez, Ana López"])
    assert rc == 1
    assert visto["candidatos"] == ["Juan Pérez", "Ana López"]
    assert "no respondió" in capsys.readouterr().err
