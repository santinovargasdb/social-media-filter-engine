"""Tests de redes/tiktok.py — partes puras (los selectores reales se tunean en vivo)."""
import redes
from redes import tiktok


def test_registro_incluye_tiktok():
    assert redes.POR_NOMBRE["tiktok"] is tiktok


def test_url_busqueda_encodea_el_termino():
    url = tiktok.url_busqueda("Javier Milei")
    assert url == "https://www.tiktok.com/search/video?q=Javier%20Milei"


def test_login_completado_fuera_de_login_y_signup():
    assert tiktok.login_completado("https://www.tiktok.com/foryou") is True
    assert tiktok.login_completado("https://www.tiktok.com/") is True
    assert tiktok.login_completado("https://www.tiktok.com/login") is False
    assert tiktok.login_completado("https://www.tiktok.com/login/phone-or-email") is False
    assert tiktok.login_completado("https://www.tiktok.com/signup") is False


def test_links_de_videos_filtra_dedupea_y_corta():
    hrefs = [
        "https://www.tiktok.com/@a/video/111",
        "https://www.tiktok.com/@a",                  # perfil: afuera
        "https://www.tiktok.com/@a/video/111",        # repetido: afuera
        "https://www.tiktok.com/@b/video/222",
        "https://www.tiktok.com/@c/video/333",
    ]
    assert tiktok.links_de_videos(hrefs, 2) == [
        "https://www.tiktok.com/@a/video/111",
        "https://www.tiktok.com/@b/video/222",
    ]


def test_links_de_videos_menos_que_pedidos():
    assert tiktok.links_de_videos(["https://t/@a/video/1"], 5) == ["https://t/@a/video/1"]
    assert tiktok.links_de_videos([], 3) == []
