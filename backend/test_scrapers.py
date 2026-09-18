"""
Tests de la capa de scraping (scrapers.py). NO pegan a la red: se mockea la llamada
HTTP aislada (_x_http). Las fixtures representan la forma DOCUMENTADA de la API de X
(twitterapi.io) — VERIFICAR contra la API real al conectar la key (el parseo está
aislado en _tweet_to_src, así ajustar nombres de campo es un cambio de una línea).

Correr desde backend/:  python -m pytest -q
"""
import pytest

import scrapers


def _fixture_page(cursor="", has_next=False, ids=("1", "2")):
    return {
        "tweets": [
            {"id": i, "url": f"https://x.com/usuario{i}/status/{i}",
             "text": f"Javier Milei posteo {i}", "createdAt": "2026-09-01",
             "author": {"userName": f"usuario{i}"}}
            for i in ids
        ],
        "has_next_page": has_next,
        "next_cursor": cursor,
    }


def test_tweet_to_src_mapea_al_shape_crudo():
    tw = {"id": "42", "url": "https://x.com/pepe/status/42", "text": "hola Milei",
          "createdAt": "2026-09-01", "author": {"userName": "pepe"}}
    src = scrapers._tweet_to_src(tw)
    assert src["url"] == "https://x.com/pepe/status/42"
    assert src["snippet"] == "hola Milei"
    assert src["network"] == "twitter"
    assert src["date"] == "2026-09-01"


def test_scrape_x_devuelve_posts_con_el_shape_de_fetch_raw_posts(monkeypatch):
    monkeypatch.setattr(scrapers, "X_SCRAPER_API_KEY", "k")
    monkeypatch.setattr(scrapers, "_x_http", lambda q, c: (_fixture_page(has_next=False), True))
    posts, up = scrapers.scrape_network("twitter", "Javier Milei elecciones", keywords=["milei"])
    assert up is False and len(posts) == 2
    p = posts[0]
    # Mismo shape que produce _normalize_raw_post (lo reusa).
    assert p["network"] == "twitter" and "/status/" in p["post_url"] and p["text"]
    assert set(p) == {"id", "network", "author", "author_url", "text", "date", "post_url",
                      "matched_terms", "video_url"}


def test_scrape_x_pagina_siguiendo_el_cursor(monkeypatch):
    monkeypatch.setattr(scrapers, "X_SCRAPER_API_KEY", "k")
    llamados = []

    def fake_http(query, cursor):
        llamados.append(cursor)
        if not cursor:
            return _fixture_page(cursor="C2", has_next=True, ids=("1", "2")), True
        return _fixture_page(has_next=False, ids=("3", "4")), True

    monkeypatch.setattr(scrapers, "_x_http", fake_http)
    posts, up = scrapers.scrape_network("twitter", "Milei", pages=2)
    assert llamados == [None, "C2"]     # arrancó sin cursor y siguió next_cursor
    assert len(posts) == 4


def test_scrape_x_error_http_marca_upstream(monkeypatch):
    monkeypatch.setattr(scrapers, "X_SCRAPER_API_KEY", "k")
    monkeypatch.setattr(scrapers, "_x_http", lambda q, c: (None, False))
    posts, up = scrapers.scrape_network("twitter", "Milei")
    assert posts == [] and up is True   # igual que fetch_raw_posts ante upstream


class _Resp:
    def __init__(self, code, data=None):
        self.status_code = code
        self._data = data or {}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise scrapers.requests.exceptions.HTTPError(str(self.status_code))
    def json(self):
        return self._data


def test_x_http_reintenta_ante_429(monkeypatch):
    """El 429 (rate-limit por ráfaga) es transitorio: se reintenta con backoff en vez
    de perder la búsqueda — esa era la causa de que trajera pocos posts."""
    monkeypatch.setattr(scrapers, "X_SCRAPER_API_KEY", "k")
    monkeypatch.setattr(scrapers, "_X_BACKOFF", 0)  # sin espera real en el test
    llamadas = {"n": 0}

    def fake_get(url, params, headers, timeout):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return _Resp(429)                     # primera: throttled
        return _Resp(200, {"tweets": [], "has_next_page": False, "next_cursor": ""})

    monkeypatch.setattr(scrapers.requests, "get", fake_get)
    data, ok = scrapers._x_http("Javier Milei", None)
    assert ok is True and llamadas["n"] == 2       # reintentó tras el 429 y salió OK


def test_x_http_devuelve_none_si_persiste_el_error(monkeypatch):
    monkeypatch.setattr(scrapers, "X_SCRAPER_API_KEY", "k")
    monkeypatch.setattr(scrapers, "_X_BACKOFF", 0)
    monkeypatch.setattr(scrapers.requests, "get", lambda url, **kw: _Resp(429))
    data, ok = scrapers._x_http("q", None)
    assert data is None and ok is False


def test_scrape_network_ig_y_tiktok_no_implementados_todavia():
    with pytest.raises(NotImplementedError):
        scrapers.scrape_network("instagram", "Milei")
    with pytest.raises(NotImplementedError):
        scrapers.scrape_network("tiktok", "Milei")
