"""Tests de redes/tiktok.py — partes puras (los selectores reales se tunean en vivo)."""
import pytest

import browser
import redes
from redes import tiktok


class _Locator:
    def __init__(self, n=0):
        self._n = n

    def count(self):
        return self._n


class _PaginaFalsa:
    def __init__(self, url="https://www.tiktok.com/search/video?q=x", captcha=0):
        self.url = url
        self._captcha = captcha

    def locator(self, selector):
        return _Locator(self._captcha if "captcha" in selector else 0)


def test_verificar_sesion_redirect_a_login_quema():
    """El redirect a /login sí es evidencia fuerte: la sesión no sirve."""
    with pytest.raises(browser.SesionInvalidaError):
        tiktok._verificar_sesion(_PaginaFalsa(url="https://www.tiktok.com/login"))


def test_verificar_sesion_captcha_es_challenge_temporal():
    """El captcha desafía a la máquina/IP, no a la cuenta: NO debe quemarla."""
    with pytest.raises(browser.DesafioTemporalError):
        tiktok._verificar_sesion(_PaginaFalsa(captcha=1))


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


def test_resultados_timeout_es_challenge_temporal(monkeypatch, tmp_path):
    """La línea que quemó tt1/tt2 el 2026-09-30: resultados que no aparecen en 30s
    son captcha/anti-bot probable, NO sesión muerta — no debe quemar la cuenta."""
    pw = pytest.importorskip("playwright.sync_api")
    from contextlib import contextmanager

    class _Keyboard:
        def type(self, *a, **k):
            pass

        def press(self, *a, **k):
            pass

    class _LocatorBusqueda(_Locator):
        def click(self):
            pass

        @property
        def first(self):
            return self

    class _PaginaBusqueda:
        url = "https://www.tiktok.com/foryou"
        keyboard = _Keyboard()

        def goto(self, *a, **k):
            pass

        def locator(self, selector):
            return _LocatorBusqueda(1 if selector == "[data-e2e='nav-search']" else 0)

        def wait_for_load_state(self, *a, **k):
            pass

        def wait_for_selector(self, selector, timeout=None):
            raise pw.TimeoutError("sin resultados")

    @contextmanager
    def fake_sesion(sesion, viewport, headless):
        yield _PaginaBusqueda()

    monkeypatch.setattr(browser, "pagina_con_sesion", fake_sesion)
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    cfg = {"espera_entre_scrolls": [0, 0], "viewport": [100, 100], "headless": True}
    with pytest.raises(browser.DesafioTemporalError):
        tiktok.capturar(tmp_path / "s.json", "Javier Milei", cfg,
                        {"scrolls_por_candidato": 1}, tmp_path, "p", [])


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


def test_resolver_headless_tiktok_visible_por_defecto():
    # TikTok sirve un captcha-slider a los browsers headless (verificado en vivo),
    # así que corre VISIBLE aunque el config global esté en headless.
    assert tiktok._resolver_headless({"headless": True}, {}) is False
    assert tiktok._resolver_headless({"headless": False}, {}) is False


def test_resolver_headless_respeta_override_por_red():
    # Un override explícito en cfg_red gana (por si TikTok vuelve a tolerar headless).
    assert tiktok._resolver_headless({"headless": True}, {"headless": True}) is True
    assert tiktok._resolver_headless({"headless": False}, {"headless": False}) is False
