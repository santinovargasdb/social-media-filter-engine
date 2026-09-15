"""
Tests de la Capa 2 (fetcher): helpers puros de armado de query y limpieza del
raw de SerpAPI. No se pega a la red (search_serpapi no se invoca acá).

Correr desde backend/:  python -m pytest -q
"""
import datetime
import fetcher as ft


# ── _build_accounts_filter ────────────────────────────────────────────────────
def test_build_accounts_filter():
    assert ft._build_accounts_filter(["@smata", "user2"]) == "(from:smata OR from:user2)"
    assert ft._build_accounts_filter([]) == ""
    assert ft._build_accounts_filter(None) == ""
    assert ft._build_accounts_filter(["@user"]) == "(from:user)"


def test_build_accounts_filter_limpia_vacios():
    assert ft._build_accounts_filter(["", "  ", "@valido"]) == "(from:valido)"


# ── _clean_serp_results ───────────────────────────────────────────────────────
def test_clean_serp_descarta_sin_texto_y_dedup():
    items = [
        {"url": "u1", "snippet": "hola", "title": "t1"},
        {"url": "u1", "snippet": "otro", "title": "t2"},   # url duplicada -> fuera
        {"url": "u2", "snippet": "hola", "title": "t3"},   # snippet duplicado -> fuera
        {"url": "u3", "snippet": "", "title": ""},          # sin texto -> fuera
        {"url": "u4", "snippet": "nuevo", "title": "t4"},
    ]
    out = ft._clean_serp_results(items)
    urls = [o["url"] for o in out]
    assert urls == ["u1", "u4"]


# ── _date_to_qdr ──────────────────────────────────────────────────────────────
def test_date_to_qdr_none_y_invalida():
    assert ft._date_to_qdr(None) is None
    assert ft._date_to_qdr("no-es-fecha") is None


def test_date_to_qdr_rangos():
    hoy = datetime.date.today()
    assert ft._date_to_qdr(hoy.isoformat()) == "d"
    assert ft._date_to_qdr((hoy - datetime.timedelta(days=5)).isoformat()) == "w"
    assert ft._date_to_qdr((hoy - datetime.timedelta(days=20)).isoformat()) == "m"
    assert ft._date_to_qdr((hoy - datetime.timedelta(days=200)).isoformat()) is None


# ── B.4 · _geo_params ─────────────────────────────────────────────────────────
def test_geo_params_mapea_pais_a_gl_hl():
    assert ft._geo_params("ar") == ("ar", "es")
    assert ft._geo_params("br") == ("br", "pt")
    assert ft._geo_params("us") == ("us", "en")
    assert ft._geo_params("ES") == ("es", "es")   # normaliza a minúsculas


def test_geo_params_default_y_desconocido():
    assert ft._geo_params(None) == ("ar", "es")     # default Argentina
    assert ft._geo_params("") == ("ar", "es")
    # País sin idioma mapeado: gl pasa igual, hl cae a 'es'.
    assert ft._geo_params("za") == ("za", "es")


# ── C.1 · _parse_result_date ──────────────────────────────────────────────────
def test_parse_result_date_absolutas():
    assert ft._parse_result_date("2024-06-02") == datetime.date(2024, 6, 2)
    assert ft._parse_result_date("Jun 2, 2024") == datetime.date(2024, 6, 2)
    assert ft._parse_result_date("2 ene 2023") == datetime.date(2023, 1, 2)
    assert ft._parse_result_date("15/03/2024") == datetime.date(2024, 3, 15)


def test_parse_result_date_relativas():
    hoy = datetime.date.today()
    assert ft._parse_result_date("hoy") == hoy
    assert ft._parse_result_date("ayer") == hoy - datetime.timedelta(days=1)
    assert ft._parse_result_date("hace 3 días") == hoy - datetime.timedelta(days=3)
    assert ft._parse_result_date("hace un mes") == hoy - datetime.timedelta(days=30)
    assert ft._parse_result_date("2 months ago") == hoy - datetime.timedelta(days=60)


def test_parse_result_date_no_parseable():
    assert ft._parse_result_date("") is None
    assert ft._parse_result_date(None) is None
    assert ft._parse_result_date("sin fecha clara") is None


# ── C.1 · _filter_results_by_date (hard-stop best-effort) ─────────────────────
def test_filter_results_by_date_destruye_viejos_conserva_sin_fecha():
    items = [
        {"url": "a", "date": "2024-01-01"},   # viejo -> destruido
        {"url": "b", "date": "2026-05-30"},   # nuevo -> queda
        {"url": "c", "date": ""},             # sin fecha -> se conserva (best-effort)
        {"url": "d", "date": "ni idea"},      # no parseable -> se conserva
    ]
    out = ft._filter_results_by_date(items, "2026-01-01", "tiktok")
    assert [o["url"] for o in out] == ["b", "c", "d"]


def test_filter_results_by_date_fecha_floor_invalida_no_filtra():
    items = [{"url": "a", "date": "2020-01-01"}]
    # Si la fecha pedida es inválida, no se filtra nada.
    assert ft._filter_results_by_date(items, "no-fecha", "instagram") == items


# ── Fallback geográfico de SerpAPI ────────────────────────────────────────────
class _FakeResp:
    def __init__(self, data):
        self._data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self._data


def test_serpapi_geo_fallback_recupera_sin_hl(monkeypatch):
    """Si la petición con región (hl) falla, reintenta sin hl y recupera."""
    monkeypatch.setattr(ft, "SERPAPI_API_KEY", "k")
    intentos = []

    def fake_get(url, params, timeout):
        intentos.append(dict(params))
        if "hl" in params:  # primer intento (región completa) -> falla
            raise ft.requests.exceptions.ConnectionError("boom región")
        return _FakeResp({"organic_results": [{"title": "t", "snippet": "s", "link": "https://x/1", "date": ""}]})

    monkeypatch.setattr(ft.requests, "get", fake_get)
    out = ft.search_serpapi("noticias", network="twitter", country="jp")
    assert out and out[0]["url"] == "https://x/1"
    assert any("hl" in p for p in intentos) and any("hl" not in p for p in intentos)


def test_serpapi_geo_fallback_todo_falla_devuelve_none(monkeypatch):
    monkeypatch.setattr(ft, "SERPAPI_API_KEY", "k")

    def fake_get(url, params, timeout):
        raise ft.requests.exceptions.ConnectionError("siempre falla")

    monkeypatch.setattr(ft.requests, "get", fake_get)
    assert ft.search_serpapi("noticias", network="twitter", country="jp") is None


def test_search_serpapi_web_parsea_organicos(monkeypatch):
    import fetcher
    fake = {"organic_results": [
        {"title": "Encuesta X", "snippet": "Milei 36%", "link": "https://n/1", "date": "2026-08-01"},
        {"title": "Nota Y", "snippet": "Kicillof 32%", "link": "https://n/2", "date": ""},
    ]}
    monkeypatch.setattr(fetcher, "SERPAPI_API_KEY", "test-key")
    monkeypatch.setattr(fetcher, "_serpapi_get_with_geo_fallback", lambda params, tag: fake)
    out = fetcher.search_serpapi_web("Opinaia encuesta", max_results=5, country="ar")
    assert out == [
        {"title": "Encuesta X", "snippet": "Milei 36%", "url": "https://n/1", "date": "2026-08-01"},
        {"title": "Nota Y", "snippet": "Kicillof 32%", "url": "https://n/2", "date": ""},
    ]


def test_search_serpapi_web_sin_key_devuelve_none(monkeypatch):
    import fetcher
    monkeypatch.setattr(fetcher, "SERPAPI_API_KEY", "")
    assert fetcher.search_serpapi_web("q") is None


def test_search_serpapi_web_upstream_none(monkeypatch):
    import fetcher
    monkeypatch.setattr(fetcher, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(fetcher, "_serpapi_get_with_geo_fallback", lambda params, tag: None)
    assert fetcher.search_serpapi_web("q") is None


def test_search_serpapi_web_pasa_tbs(monkeypatch):
    import fetcher
    captured = {}

    def fake(params, tag):
        captured.update(params)
        return {"organic_results": []}

    monkeypatch.setattr(fetcher, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(fetcher, "_serpapi_get_with_geo_fallback", fake)
    fetcher.search_serpapi_web("q", tbs="cdr:1,cd_min:05/18/2026,cd_max:09/15/2026")
    assert captured.get("tbs") == "cdr:1,cd_min:05/18/2026,cd_max:09/15/2026"
    # Sin tbs, no debe agregar la clave.
    captured.clear()
    fetcher.search_serpapi_web("q")
    assert "tbs" not in captured
