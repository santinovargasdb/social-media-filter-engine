"""Tests de run.py — orquestación con browser/vision/store mockeados (sin red)."""
import json
from pathlib import Path

import pytest

import run
import browser as browser_mod
from redes import twitter


POST_MILEI = {"texto": "Gran discurso de Milei", "autor": "@fan", "fecha": "2 h",
              "red": "twitter", "es_electoral": True,
              "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9}],
              "cita": "Gran discurso"}
POST_RUIDO = {"texto": "Partidazo de River", "autor": "@futbol", "fecha": "3 h",
              "red": "twitter", "es_electoral": False, "candidatos": [], "cita": ""}
POST_TIKTOK = {"texto": "Milei imparable en TikTok", "autor": "@tt", "fecha": "1 d",
               "red": "tiktok", "es_electoral": True,
               "candidatos": [{"nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.8}],
               "cita": "imparable"}


def _cfg(**over):
    cfg = run.cargar_config(Path("no-existe.json"))
    cfg.update({"espera_entre_candidatos": [0, 0], "espera_entre_scrolls": [0, 0]})
    cfg.update(over)
    return cfg


def test_cargar_config_defaults_y_override(tmp_path):
    assert run.cargar_config(tmp_path / "nada.json")["redes"] == {
        "twitter": {"scrolls_por_candidato": 3}}
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"redes": {"twitter": {"scrolls_por_candidato": 7}}}),
                    encoding="utf-8")
    cfg = run.cargar_config(ruta)
    assert cfg["redes"]["twitter"]["scrolls_por_candidato"] == 7
    assert cfg["headless"] is True  # el resto conserva el default


def test_cargar_config_traduce_formato_legacy(tmp_path):
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps({"red": "twitter", "scrolls_por_candidato": 5,
                                "headless": False}), encoding="utf-8")
    cfg = run.cargar_config(ruta)
    assert cfg["redes"] == {"twitter": {"scrolls_por_candidato": 5}}
    assert "red" not in cfg and "scrolls_por_candidato" not in cfg
    assert cfg["headless"] is False


def test_filtrar_redes_acota_y_valida():
    cfg = {"redes": {"twitter": {"a": 1}, "tiktok": {"b": 2}}}
    assert run.filtrar_redes(dict(cfg), "tiktok")["redes"] == {"tiktok": {"b": 2}}
    assert run.filtrar_redes(dict(cfg), None)["redes"] == cfg["redes"]
    with pytest.raises(SystemExit):
        run.filtrar_redes(dict(cfg), "instagram")


def test_armar_bloque_mapea_al_shape_de_electoral():
    bloque = run.armar_bloque([POST_MILEI, POST_RUIDO], "twitter",
                              busquedas=2, crudos=5, errores=1)
    assert bloque["network"] == "twitter"
    assert bloque["posts_by_id"]["twitter_0"]["author"] == "@fan"
    assert bloque["posts_by_id"]["twitter_0"]["author_url"] == "https://x.com/fan"
    assert bloque["posts_by_id"]["twitter_1"]["author_url"] == "https://x.com/futbol"
    assert bloque["analysis"][0]["candidatos"][0]["nombre"] == "Javier Milei"
    assert bloque["analysis"][1]["es_electoral"] is False
    assert bloque["status"] == {"red": "twitter", "busquedas": 2, "crudos": 5,
                                "errores": 1, "encontrados": 2, "analizados": 2}


def test_armar_bloque_autor_sin_arroba_no_arma_url():
    bloque = run.armar_bloque([dict(POST_MILEI, autor="Nombre Visible")], "twitter", 1, 1, 0)
    assert bloque["posts_by_id"]["twitter_0"]["author_url"] == ""


def test_armar_payload_reusa_electoral():
    bloque = run.armar_bloque([POST_MILEI, POST_RUIDO], "twitter", 2, 5, 0)
    payload = run.armar_payload([bloque], ["warn-previo"])
    assert payload["candidatos"][0]["nombre"] == "Javier Milei"
    assert payload["candidatos"][0]["por_red"] == {"twitter": 1}
    assert payload["evidencia"][0]["candidato"] == "Javier Milei"
    assert isinstance(payload["comparacion"], list)
    meta = payload["meta"]
    assert meta["total_posts"] == 2 and meta["posts_electorales"] == 1
    assert meta["bloques"] == [bloque["status"]]
    assert "termómetro" in meta["disclaimer"] or "termometro" in meta["disclaimer"].lower()
    assert "warn-previo" in meta["warnings"]


def test_armar_payload_fusiona_bloques_por_red():
    b_tw = run.armar_bloque([POST_MILEI], "twitter", busquedas=1, crudos=1, errores=0)
    b_tt = run.armar_bloque([POST_TIKTOK], "tiktok", busquedas=1, crudos=2, errores=1)
    payload = run.armar_payload([b_tw, b_tt], [])
    assert payload["candidatos"][0]["nombre"] == "Javier Milei"
    assert payload["candidatos"][0]["por_red"] == {"twitter": 1, "tiktok": 1}
    meta = payload["meta"]
    assert meta["total_posts"] == 2 and meta["posts_electorales"] == 2
    assert [b["red"] for b in meta["bloques"]] == ["twitter", "tiktok"]


def test_author_url_por_red():
    assert run._author_url("@fan", "twitter") == "https://x.com/fan"
    assert run._author_url("@fan", "tiktok") == "https://www.tiktok.com/@fan"
    assert run._author_url("Nombre Visible", "tiktok") == ""


def test_capturar_candidato_rota_ante_sesion_invalida(tmp_path, monkeypatch):
    pool = {"cuentas": [
        {"alias": "muerta", "estado": "activa", "ultima_vez": "", "notas": ""},
        {"alias": "viva", "estado": "activa", "ultima_vez": "2026-09-01T00:00:00+00:00", "notas": ""},
    ]}
    usadas = []
    def fake_capturar(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings):
        usadas.append(sesion.name)
        if sesion.name == "muerta.json":
            raise browser_mod.SesionInvalidaError("challenge")
        return [{"ruta": tmp_path / "cap-1.png", "contexto": ""}]
    monkeypatch.setattr(twitter, "capturar", fake_capturar)
    warnings = []
    capturas = run.capturar_candidato("twitter", _cfg(), pool, "Javier Milei", tmp_path, warnings)
    assert usadas == ["muerta.json", "viva.json"]
    assert len(capturas) == 1
    assert pool["cuentas"][0]["estado"] == "quemada"
    assert any("quemada" in w for w in warnings)


def test_capturar_candidato_sin_cuentas_devuelve_vacio(tmp_path):
    warnings = []
    rutas = run.capturar_candidato("twitter", _cfg(), {"cuentas": []}, "X", tmp_path, warnings)
    assert rutas == [] and any("Sin cuentas activas" in w for w in warnings)


def _preparar_correr(monkeypatch, tmp_path, posts_por_captura, escritos):
    """Mockea todo lo externo de correr(): cuentas, browser, vision, store, esperas."""
    pool = {"cuentas": [{"alias": "a", "estado": "activa", "ultima_vez": "", "notas": ""}]}
    monkeypatch.setattr(run.accounts, "cargar_pool", lambda ruta=None, red="twitter": pool)
    monkeypatch.setattr(run.accounts, "guardar_pool", lambda p, ruta=None, red="twitter": None)
    monkeypatch.setattr(run, "capturar_candidato",
                        lambda red_nombre, cfg, pool, cand, carpeta, warnings: [
                            {"ruta": tmp_path / f"{red_nombre}-{cand}.png", "contexto": ""}])
    monkeypatch.setattr(run.vision, "read_capture",
                        lambda ruta, red, candidatos=None, contexto="": posts_por_captura)
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
    def explota(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings):
        raise RuntimeError("chromium se murió")
    monkeypatch.setattr(twitter, "capturar", explota)
    warnings = []
    capturas = run.capturar_candidato("twitter", _cfg(), pool, "Javier Milei", tmp_path, warnings)
    assert capturas == []
    assert pool["cuentas"][0]["estado"] == "activa"  # NO quemada
    assert any("error del navegador" in w for w in warnings)


def test_correr_pasa_candidatos_a_vision(monkeypatch, tmp_path):
    """El prompt de visión recibe la lista de candidatos del config."""
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    vistos = []
    monkeypatch.setattr(run.vision, "read_capture",
                        lambda ruta, red, candidatos=None, contexto="": vistos.append(candidatos) or [POST_MILEI])
    run.correr(_cfg(candidatos=["Solo Uno"]), dry_run=False)
    assert vistos and vistos[0] == ["Solo Uno"]


def test_correr_multi_red_arma_un_bloque_por_red(monkeypatch, tmp_path):
    escritos = []
    _preparar_correr(monkeypatch, tmp_path, [POST_MILEI], escritos)
    # vision devuelve el post según la red que se está capturando:
    monkeypatch.setattr(run.vision, "read_capture",
                        lambda ruta, red, candidatos=None, contexto="":
                        [POST_MILEI] if red == "twitter" else [POST_TIKTOK])
    cfg = _cfg(candidatos=["Javier Milei"])
    cfg["redes"] = {"twitter": {"scrolls_por_candidato": 3},
                    "tiktok": {"scrolls_por_candidato": 3, "videos_comentarios": 2,
                               "scrolls_comentarios": 2, "candidatos_comentarios": []}}
    rc = run.correr(cfg, dry_run=False)
    assert rc == 0 and len(escritos) == 1
    assert [b["red"] for b in escritos[0]["meta"]["bloques"]] == ["twitter", "tiktok"]
    assert escritos[0]["candidatos"][0]["por_red"] == {"twitter": 1, "tiktok": 1}
