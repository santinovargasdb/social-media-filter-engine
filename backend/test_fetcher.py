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


# ── _date_to_tbs (rango de fechas real, cualquier antigüedad) ─────────────────
def test_date_to_tbs_rango_para_cualquier_antiguedad():
    # 9 meses atrás -> rango cd_min..cd_max (no "sin filtro", como pasaba con el qdr).
    tbs = ft._date_to_tbs("2026-01-17", today=datetime.date(2026, 9, 17))
    assert tbs == "cdr:1,cd_min:01/17/2026,cd_max:09/17/2026"


def test_date_to_tbs_none_invalida_y_futura():
    assert ft._date_to_tbs(None) is None
    assert ft._date_to_tbs("no-es-fecha") is None
    # Fecha futura -> None (no tiene sentido un rango invertido).
    assert ft._date_to_tbs("2027-01-01", today=datetime.date(2026, 9, 17)) is None


def test_search_serpapi_usa_rango_de_fechas_para_plazo_largo(monkeypatch):
    """Un 'desde' de 9 meses debe traducirse a un rango de fechas real (cd_min/cd_max),
    no descartarse — que era la causa de que ampliar el plazo no cambiara nada."""
    captured = {}

    def fake(params, tag):
        captured.update(params)
        return {"organic_results": []}

    monkeypatch.setattr(ft, "SERPAPI_API_KEY", "k")
    monkeypatch.setattr(ft, "_serpapi_get_with_geo_fallback", fake)
    hace9meses = (datetime.date.today() - datetime.timedelta(days=270)).isoformat()
    ft.search_serpapi("smata", network="twitter", fecha_desde=hace9meses)
    assert captured.get("tbs", "").startswith("cdr:1,cd_min:")


def test_search_serpapi_pagina_multiples_paginas(monkeypatch):
    """Con pages>1 debe pedir páginas sucesivas (start=0,10,20…) y acumular, en vez
    de traer solo los primeros ~10 (el techo que hacía que 9 meses ≈ 2 meses)."""
    monkeypatch.setattr(ft, "SERPAPI_API_KEY", "k")
    starts = []

    def fake(params, tag):
        idx = params.get("start", 0)
        starts.append(idx)
        if idx >= 20:  # tercera página vacía -> corta
            return {"organic_results": []}
        return {"organic_results": [
            {"title": f"t{idx}", "snippet": f"s{idx}", "link": f"https://x/{idx}", "date": ""}]}

    monkeypatch.setattr(ft, "_serpapi_get_with_geo_fallback", fake)
    out = ft.search_serpapi("q", network="twitter", pages=3)
    assert starts == [0, 10, 20]     # paginó hasta encontrar la página vacía
    assert len(out) == 2             # acumuló las dos páginas con resultados


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
