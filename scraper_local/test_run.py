"""Tests de run.py — orquestación con browser/vision/store mockeados (sin red)."""
import json
from pathlib import Path

import run
import browser as browser_mod


POST_MILEI = {"texto": "Gran discurso de Milei", "autor": "@fan", "fecha": "2 h",
              "red": "twitter", "es_electoral": True,
              "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}],
              "cita": "Gran discurso"}
POST_RUIDO = {"texto": "Partidazo de River", "autor": "@futbol", "fecha": "3 h",
              "red": "twitter", "es_electoral": False, "candidatos": [], "cita": ""}


def _cfg(**over):
    cfg = run.cargar_config(Path("no-existe.json"))
    cfg.update({"espera_entre_candidatos": [0, 0], "espera_entre_scrolls": [0, 0]})
    cfg.update(over)
    return cfg


def test_cargar_config_defaults_y_override(tmp_path):
    assert run.cargar_config(tmp_path / "nada.json")["scrolls_por_candidato"] == 3
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"scrolls_por_candidato": 7}), encoding="utf-8")
    cfg = run.cargar_config(ruta)
    assert cfg["scrolls_por_candidato"] == 7
    assert cfg["red"] == "twitter"  # el resto conserva el default


def test_armar_bloque_mapea_al_shape_de_electoral():
    bloque = run.armar_bloque([POST_MILEI, POST_RUIDO], "twitter",
                              busquedas=2, crudos=5, errores=1)
    assert bloque["network"] == "twitter"
    assert bloque["posts_by_id"]["Post_0"]["author"] == "@fan"
    assert bloque["posts_by_id"]["Post_0"]["author_url"] == "https://x.com/fan"
    assert bloque["posts_by_id"]["Post_1"]["author_url"] == "https://x.com/futbol"
    assert bloque["analysis"][0]["candidatos"][0]["nombre"] == "Javier Milei"
    assert bloque["analysis"][1]["es_electoral"] is False
    assert bloque["status"] == {"red": "twitter", "busquedas": 2, "crudos": 5,
                                "errores": 1, "encontrados": 2, "analizados": 2}


def test_armar_bloque_autor_sin_arroba_no_arma_url():
    bloque = run.armar_bloque([dict(POST_MILEI, autor="Nombre Visible")], "twitter", 1, 1, 0)
    assert bloque["posts_by_id"]["Post_0"]["author_url"] == ""


def test_armar_payload_reusa_electoral():
    bloque = run.armar_bloque([POST_MILEI, POST_RUIDO], "twitter", 2, 5, 0)
    payload = run.armar_payload(bloque, ["warn-previo"])
    assert payload["candidatos"][0]["nombre"] == "Javier Milei"
    assert payload["candidatos"][0]["por_red"] == {"twitter": 1}
    assert payload["evidencia"][0]["candidato"] == "Javier Milei"
    assert isinstance(payload["comparacion"], list)
    meta = payload["meta"]
    assert meta["total_posts"] == 2 and meta["posts_electorales"] == 1
    assert meta["bloques"] == [bloque["status"]]
    assert "termómetro" in meta["disclaimer"] or "termometro" in meta["disclaimer"].lower()
    assert "warn-previo" in meta["warnings"]


def test_capturar_candidato_rota_ante_sesion_invalida(tmp_path, monkeypatch):
    pool = {"cuentas": [
        {"alias": "muerta", "estado": "activa", "ultima_vez": "", "notas": ""},
        {"alias": "viva", "estado": "activa", "ultima_vez": "2026-09-01T00:00:00+00:00", "notas": ""},
    ]}
    usadas = []
    def fake_capturar(sesion, termino, **kw):
        usadas.append(sesion.name)
        if sesion.name == "muerta.json":
            raise browser_mod.SesionInvalidaError("challenge")
        return [tmp_path / "cap-1.png"]
    monkeypatch.setattr(run.browser, "capturar_busqueda", fake_capturar)
    warnings = []
    rutas = run.capturar_candidato(_cfg(), pool, "Javier Milei", tmp_path, warnings)
    assert usadas == ["muerta.json", "viva.json"]
    assert len(rutas) == 1
    assert pool["cuentas"][0]["estado"] == "quemada"
    assert any("quemada" in w for w in warnings)


def test_capturar_candidato_sin_cuentas_devuelve_vacio(tmp_path):
    warnings = []
    rutas = run.capturar_candidato(_cfg(), {"cuentas": []}, "X", tmp_path, warnings)
    assert rutas == [] and any("Sin cuentas activas" in w for w in warnings)


def _preparar_correr(monkeypatch, tmp_path, posts_por_captura, escritos):
    """Mockea todo lo externo de correr(): cuentas, browser, vision, store, esperas."""
    pool = {"cuentas": [{"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""}]}
    monkeypatch.setattr(run.accounts, "cargar_pool", lambda ruta=None: pool)
    monkeypatch.setattr(run.accounts, "guardar_pool", lambda p, ruta=None: None)
    monkeypatch.setattr(run, "capturar_candidato",
                        lambda cfg, pool, cand, carpeta, warnings: [tmp_path / f"{cand}.png"])
    monkeypatch.setattr(run.vision, "read_capture", lambda ruta, red, candidatos=None: posts_por_captura)
    monkeypatch.setattr(run.store, "write_snapshot",
                        lambda payload, generado_en: escritos.append(payload) or True)
    monkeypatch.setattr(run.browser, "esperar_aleatorio", lambda rango: None)
    monkeypatch.setattr(run, "_limpiar_corridas_viejas", lambda conservar: None)
    monkeypatch.setattr(run, "DIR_CAPTURAS", tmp_path / "capturas")


def test_correr_feliz_sube_snapshot_deduplicado(monkeypatch, tmp_path):
    escritos = []
    # La misma captura para 2 candidatos -> el dedup deja 2 posts únicos.
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI, POST_RUIDO, POST_MILEI], escritos)
    rc = run.correr(_cfg(candidatos=["Javier Milei", "Axel Kicillof"]), dry_run=False)
    assert rc == 0
    assert len(escritos) == 1
    meta = escritos[0]["meta"]
    assert meta["total_posts"] == 2      # 6 crudos (3x2) -> 2 únicos
    assert meta["posts_electorales"] == 1
    assert escritos[0]["candidatos"][0]["nombre"] == "Javier Milei"


def test_correr_sin_electorales_no_pisa_snapshot(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_RUIDO], escritos)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    assert rc == 1 and escritos == []


def test_correr_dry_run_no_sube(monkeypatch, tmp_path, capsys):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=True)
    assert rc == 0 and escritos == []
    assert "posts_electorales" in capsys.readouterr().out


def test_correr_falla_subida_devuelve_1(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    monkeypatch.setattr(run.store, "write_snapshot", lambda payload, generado_en: False)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    assert rc == 1


def test_correr_lectura_fallida_cuenta_error(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, None, escritos)  # vision devuelve None (upstream)
    rc = run.correr(_cfg(candidatos=["Javier Milei"]), dry_run=False)
    assert rc == 1  # sin posts electorales -> no pisa
    assert escritos == []


def test_capturar_candidato_error_inesperado_no_quema_ni_aborta(tmp_path, monkeypatch):
    """Un crash de Playwright (no SesionInvalidaError) devuelve [] con warning,
    sin marcar la cuenta como quemada."""
    pool = {"cuentas": [{"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""}]}
    def explota(sesion, termino, **kw):
        raise RuntimeError("chromium se murió")
    monkeypatch.setattr(run.browser, "capturar_busqueda", explota)
    warnings = []
    rutas = run.capturar_candidato(_cfg(), pool, "Javier Milei", tmp_path, warnings)
    assert rutas == []
    assert pool["cuentas"][0]["estado"] == "activa"  # NO quemada
    assert any("error del navegador" in w for w in warnings)


def test_correr_pasa_candidatos_a_vision(monkeypatch, tmp_path):
    """El prompt de visión recibe la lista de candidatos del config."""
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    vistos = []
    monkeypatch.setattr(run.vision, "read_capture",
                        lambda ruta, red, candidatos=None: vistos.append(candidatos) or [POST_MILEI])
    run.correr(_cfg(candidatos=["Solo Uno"]), dry_run=False)
    assert vistos and vistos[0] == ["Solo Uno"]
