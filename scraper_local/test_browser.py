"""Tests de browser.py — la maquinaria de captura con un page falso (sin Playwright)."""
from pathlib import Path

import browser


class FakePage:
    """Simula lo mínimo de un Page de Playwright: screenshot + evaluate."""
    def __init__(self):
        self.scrolls = []

    def screenshot(self, path):
        Path(path).write_bytes(b"png-falso")

    def evaluate(self, script):
        self.scrolls.append(script)


def test_url_busqueda_encodea_termino_y_lang():
    url = browser.url_busqueda("Javier Milei")
    assert url.startswith("https://x.com/search?q=")
    assert "Javier%20Milei%20lang%3Aes" in url
    assert "f=live" in url


def test_capturar_pagina_saca_n_capturas_y_scrollea_entre_medio(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePage()
    rutas = browser.capturar_pagina(page, scrolls=3, esperas=(0, 0),
                                    carpeta=tmp_path / "caps", prefijo="milei")
    assert [r.name for r in rutas] == ["milei-1.png", "milei-2.png", "milei-3.png"]
    assert all(r.exists() for r in rutas)
    # Scrollea ENTRE capturas: n-1 scrolls para n capturas.
    assert len(page.scrolls) == 2
    assert "window.innerHeight * 0.9" in page.scrolls[0]


def test_capturar_pagina_un_scroll_no_scrollea(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePage()
    rutas = browser.capturar_pagina(page, scrolls=1, esperas=(0, 0),
                                    carpeta=tmp_path, prefijo="uno")
    assert len(rutas) == 1 and page.scrolls == []


def test_esperar_aleatorio_dentro_del_rango(monkeypatch):
    dormido = []
    monkeypatch.setattr(browser.time, "sleep", lambda s: dormido.append(s))
    monkeypatch.setattr(browser.random, "uniform", lambda a, b: (a + b) / 2)
    browser.esperar_aleatorio((2, 5))
    assert dormido == [3.5]


def test_sesion_invalida_es_exception():
    assert issubclass(browser.SesionInvalidaError, Exception)


class FakeLocator:
    def __init__(self, page):
        self.page = page
        self.first = self

    def screenshot(self, path):
        Path(path).write_bytes(b"png-elemento")


class FakePageConElemento(FakePage):
    def __init__(self):
        super().__init__()
        self.selectores_evaluados = []

    def locator(self, selector):
        return FakeLocator(self)

    def eval_on_selector(self, selector, script):
        self.selectores_evaluados.append((selector, script))


def test_capturar_elemento_screenshotea_y_scrollea_el_elemento(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePageConElemento()
    rutas = browser.capturar_elemento(page, "#comentarios", scrolls=3, esperas=(0, 0),
                                      carpeta=tmp_path / "caps", prefijo="milei-comentarios-1")
    assert [r.name for r in rutas] == ["milei-comentarios-1-1.png",
                                       "milei-comentarios-1-2.png",
                                       "milei-comentarios-1-3.png"]
    assert all(r.read_bytes() == b"png-elemento" for r in rutas)
    # Scrollea el ELEMENTO entre capturas: n-1 scrolls.
    assert len(page.selectores_evaluados) == 2
    sel, script = page.selectores_evaluados[0]
    assert sel == "#comentarios" and "scrollBy" in script and "clientHeight" in script


def test_capturar_elemento_un_scroll_no_scrollea(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "esperar_aleatorio", lambda rango: None)
    page = FakePageConElemento()
    rutas = browser.capturar_elemento(page, "#c", scrolls=1, esperas=(0, 0),
                                      carpeta=tmp_path, prefijo="uno")
    assert len(rutas) == 1 and page.selectores_evaluados == []
