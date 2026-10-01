"""Tests de redes/twitter.py — partes puras (la navegación real se verifica en vivo)."""
from contextlib import contextmanager

import pytest

import browser
import redes
from redes import twitter


def _cfg():
    return {"espera_entre_scrolls": [0, 0], "viewport": [100, 100], "headless": True}


def test_feed_timeout_es_challenge_temporal(monkeypatch, tmp_path):
    """El feed que no aparece en 30s puede ser challenge o página lenta (medido
    2026-09-30 con sesiones sanas): NO debe quemar la cuenta."""
    pw = pytest.importorskip("playwright.sync_api")

    class _Pagina:
        url = "https://x.com/search?q=x"

        def goto(self, url, timeout=None):
            pass

        def wait_for_selector(self, selector, timeout=None):
            raise pw.TimeoutError("feed no apareció")

    @contextmanager
    def fake_sesion(sesion, viewport, headless):
        yield _Pagina()

    monkeypatch.setattr(browser, "pagina_con_sesion", fake_sesion)
    with pytest.raises(browser.DesafioTemporalError):
        twitter.capturar(tmp_path / "s.json", "Javier Milei", _cfg(),
                         {"scrolls_por_candidato": 1}, tmp_path, "p", [])


def test_redirect_a_login_sigue_quemando(monkeypatch, tmp_path):
    """El redirect a /login sí es sesión muerta: conserva SesionInvalidaError."""
    pytest.importorskip("playwright.sync_api")

    class _Pagina:
        url = "https://x.com/login"

        def goto(self, url, timeout=None):
            pass

    @contextmanager
    def fake_sesion(sesion, viewport, headless):
        yield _Pagina()

    monkeypatch.setattr(browser, "pagina_con_sesion", fake_sesion)
    with pytest.raises(browser.SesionInvalidaError):
        twitter.capturar(tmp_path / "s.json", "Javier Milei", _cfg(),
                         {"scrolls_por_candidato": 1}, tmp_path, "p", [])


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
