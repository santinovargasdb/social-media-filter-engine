"""Tests de redes/twitter.py — partes puras (la navegación real se verifica en vivo)."""
import redes
from redes import twitter


def test_registro_incluye_twitter():
    assert redes.POR_NOMBRE["twitter"] is twitter


def test_url_busqueda_encodea_termino_y_lang():
    url = twitter.url_busqueda("Javier Milei")
    assert url.startswith("https://x.com/search?q=")
    assert "Javier%20Milei%20lang%3Aes" in url
    assert "f=live" in url


def test_login_completado_solo_en_home():
    assert twitter.login_completado("https://x.com/home") is True
    assert twitter.login_completado("https://x.com/login") is False
    assert twitter.login_completado("https://x.com/i/flow/login") is False
