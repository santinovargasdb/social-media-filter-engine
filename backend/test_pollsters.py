import pollsters as p


def _articles():
    return [
        {"url": "https://n/1", "title": "Nota 1", "text": "...", "date": "2026-08-01"},
        {"url": "https://n/2", "title": "Nota 2", "text": "...", "date": "2026-09-01"},
    ]


def test_parse_extraction_arma_filas_con_fuente():
    parsed = [
        {"id": "Art_0", "fecha": "2026-08-01", "filas": [
            {"candidato": "Javier Milei", "porcentaje": 36.3},
            {"candidato": "Axel Kicillof", "porcentaje": "32,3"},  # coma decimal
        ]},
    ]
    rows = p._parse_extraction(parsed, "Opinaia", _articles())
    assert len(rows) == 2
    assert rows[0] == {"consultora": "Opinaia", "fecha": "2026-08-01",
                       "candidato": "Javier Milei", "porcentaje": 36.3,
                       "fuente_url": "https://n/1", "fuente_titulo": "Nota 1"}
    assert rows[1]["porcentaje"] == 32.3  # coma normalizada


def test_parse_extraction_usa_fecha_del_articulo_si_falta():
    parsed = [{"id": "Art_1", "fecha": "", "filas": [{"candidato": "Milei", "porcentaje": 40}]}]
    rows = p._parse_extraction(parsed, "CB", _articles())
    assert rows[0]["fecha"] == "2026-09-01"  # cae a la date del artículo Art_1


def test_parse_extraction_descarta_pct_no_numerico_e_ids_inventados():
    parsed = [
        {"id": "Art_0", "filas": [{"candidato": "Milei", "porcentaje": "s/d"}]},
        {"id": "Art_99", "filas": [{"candidato": "X", "porcentaje": 10}]},  # id inexistente
    ]
    rows = p._parse_extraction(parsed, "Opinaia", _articles())
    assert rows == []


def test_dedup_latest_conserva_fecha_mas_reciente():
    rows = [
        {"consultora": "CB", "fecha": "2026-08-01", "candidato": "Javier Milei",
         "porcentaje": 40.0, "fuente_url": "u1", "fuente_titulo": "t1"},
        {"consultora": "CB", "fecha": "2026-09-01", "candidato": "javier  milei",
         "porcentaje": 42.0, "fuente_url": "u2", "fuente_titulo": "t2"},
    ]
    out = p._dedup_latest(rows)
    assert len(out) == 1 and out[0]["porcentaje"] == 42.0 and out[0]["fuente_url"] == "u2"


def test_fetch_pollster_rows_happy(monkeypatch):
    import fetcher, gemini_client as gc
    # 1 resultado por consultora, misma url; texto vía _fetch_article_text mockeado.
    monkeypatch.setattr(fetcher, "search_serpapi_web",
                        lambda q, max_results=5, country="ar", tbs=None: [
                            {"title": "Nota", "snippet": "s", "url": "https://n/x", "date": "2026-09-01"}])
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto con números")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: ([
        {"id": "Art_0", "fecha": "2026-09-01",
         "filas": [{"candidato": "Javier Milei", "porcentaje": 36.3}]},
    ], None))
    p._CACHE.clear()
    rows, warnings = p.fetch_pollster_rows(consultoras=["Opinaia", "CB Consultora"])
    # Una fila por consultora (misma url pero distinta consultora -> no se pisan).
    consultoras = sorted(r["consultora"] for r in rows)
    assert consultoras == ["CB Consultora", "Opinaia"]
    assert all(r["fuente_url"] == "https://n/x" and r["candidato"] == "Javier Milei" for r in rows)


def test_fetch_pollster_rows_una_consultora_sin_resultados(monkeypatch):
    import fetcher, gemini_client as gc

    def fake_search(q, max_results=5, country="ar", tbs=None):
        return [] if "Opinaia" in q else [{"title": "t", "snippet": "s", "url": "u", "date": ""}]

    monkeypatch.setattr(fetcher, "search_serpapi_web", fake_search)
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (
        [{"id": "Art_0", "filas": [{"candidato": "Milei", "porcentaje": 40}]}], None))
    p._CACHE.clear()
    rows, warnings = p.fetch_pollster_rows(consultoras=["Opinaia", "CB Consultora"])
    assert [r["consultora"] for r in rows] == ["CB Consultora"]
    assert any("Opinaia" in w for w in warnings)


def test_fetch_pollster_rows_cachea(monkeypatch):
    import fetcher, gemini_client as gc
    llamadas = {"n": 0}

    def fake_search(q, max_results=5, country="ar", tbs=None):
        llamadas["n"] += 1
        return [{"title": "t", "snippet": "s", "url": "u", "date": "2026-09-01"}]

    monkeypatch.setattr(fetcher, "search_serpapi_web", fake_search)
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (
        [{"id": "Art_0", "filas": [{"candidato": "Milei", "porcentaje": 40}]}], None))
    p._CACHE.clear()
    p.fetch_pollster_rows(consultoras=["Opinaia"])
    n1 = llamadas["n"]
    p.fetch_pollster_rows(consultoras=["Opinaia"])  # cache HIT -> no re-busca
    assert llamadas["n"] == n1


def test_map_espacio_a_candidato():
    # Espacios/partidos -> candidato principal (mapeo editable).
    assert p._map_espacio_a_candidato("La Libertad Avanza") == "Javier Milei"
    assert p._map_espacio_a_candidato("Unión por la Patria") == "Axel Kicillof"
    assert p._map_espacio_a_candidato("Frente de Izquierda") == "Myriam Bregman"
    # Una persona (no está en el mapa) se devuelve tal cual.
    assert p._map_espacio_a_candidato("Javier Milei") == "Javier Milei"
    assert p._map_espacio_a_candidato("Candidato Desconocido") == "Candidato Desconocido"


def test_parse_extraction_mapea_espacios_a_candidato():
    parsed = [{"id": "Art_0", "fecha": "2026-09-01", "filas": [
        {"candidato": "La Libertad Avanza", "porcentaje": 40},
        {"candidato": "Frente de Izquierda", "porcentaje": "8,0"},
    ]}]
    articles = [{"url": "u", "title": "t", "text": "...", "date": "2026-09-01"}]
    rows = p._parse_extraction(parsed, "Opinaia", articles)
    nombres = sorted(r["candidato"] for r in rows)
    assert nombres == ["Javier Milei", "Myriam Bregman"]  # espacios mapeados


def test_map_incluye_alias_provincias_unidos():
    # Variante mal escrita ("Unidos" por "Unidas") también mapea.
    assert p._map_espacio_a_candidato("Provincias Unidos") == "Juan Schiaretti"


def test_recency_tbs_arma_rango_de_fechas():
    import datetime
    tbs = p._recency_tbs(120, today=datetime.date(2026, 9, 15))
    assert tbs == "cdr:1,cd_min:05/18/2026,cd_max:09/15/2026"


def test_fetch_one_consultora_incluye_snippet_aunque_haya_texto(monkeypatch):
    """En notas con paywall/JS el cuerpo del artículo es el cascarón (login/boilerplate)
    sin números; el snippet del buscador sí trae el titular con el porcentaje. El
    snippet SIEMPRE debe llegar a Gemini, aunque el cuerpo no esté vacío."""
    import fetcher, gemini_client as gc
    monkeypatch.setattr(fetcher, "search_serpapi_web",
                        lambda q, max_results=5, country="ar", tbs=None: [
                            {"title": "Nota", "snippet": "Milei 40%, Kicillof 30%",
                             "url": "https://n/1", "date": "2026-09-01"}])
    # El cuerpo trae basura de paywall, sin los números.
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "Suscribite para seguir leyendo")
    prompts = []
    monkeypatch.setattr(gc, "run_with_rotation",
                        lambda prompt: (prompts.append(prompt) or ([{"id": "Art_0", "filas": []}], None)))
    p._fetch_one_consultora("Opinaia", "ar")
    assert "Milei 40%" in prompts[0]  # el snippet con los números llegó al prompt


def test_fetch_pollster_rows_avisa_si_no_extrae_porcentajes(monkeypatch):
    """Encuentra notas pero Gemini no extrae ningún porcentaje (paywall/prompt): NO
    debe quedar en silencio (tabla vacía sin aviso) — agrega un warning."""
    import fetcher, gemini_client as gc
    monkeypatch.setattr(fetcher, "search_serpapi_web",
                        lambda q, max_results=5, country="ar", tbs=None: [
                            {"title": "Nota", "snippet": "s", "url": "https://n/1", "date": "2026-09-01"}])
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto sin porcentajes")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: ([{"id": "Art_0", "filas": []}], None))
    p._CACHE.clear()
    rows, warnings = p.fetch_pollster_rows(consultoras=["Opinaia"])
    assert rows == []
    assert any("Opinaia" in w for w in warnings)


def test_fetch_pollster_rows_cae_a_amplia_si_recencia_da_error(monkeypatch):
    """Si la búsqueda con recencia devuelve None (p. ej. el tbs de fechas la rompe),
    igual reintenta SIN filtro en vez de rendirse con 'no respondió'."""
    import fetcher, gemini_client as gc

    def fake_search(q, max_results=5, country="ar", tbs=None):
        return None if tbs else [{"title": "t", "snippet": "Milei 40%", "url": "u", "date": "2026-01-10"}]

    monkeypatch.setattr(fetcher, "search_serpapi_web", fake_search)
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (
        [{"id": "Art_0", "filas": [{"candidato": "Javier Milei", "porcentaje": 40}]}], None))
    p._CACHE.clear()
    rows, warnings = p.fetch_pollster_rows(consultoras=["Opinaia"])
    assert len(rows) == 1 and rows[0]["candidato"] == "Javier Milei"


def test_fetch_pollster_rows_cae_a_busqueda_amplia_si_no_hay_recientes(monkeypatch):
    """Primero busca con filtro de recencia; si eso viene vacío, reintenta SIN el
    filtro (mejor una nota más vieja que ninguna)."""
    import fetcher, gemini_client as gc
    llamadas = []

    def fake_search(q, max_results=5, country="ar", tbs=None):
        llamadas.append(tbs)
        # Con recencia (tbs presente) -> vacío; sin recencia -> devuelve una nota.
        return [] if tbs else [{"title": "t", "snippet": "s", "url": "u", "date": "2026-01-10"}]

    monkeypatch.setattr(fetcher, "search_serpapi_web", fake_search)
    monkeypatch.setattr(p, "_fetch_article_text", lambda url: "texto")
    monkeypatch.setattr(gc, "run_with_rotation", lambda prompt: (
        [{"id": "Art_0", "filas": [{"candidato": "Javier Milei", "porcentaje": 40}]}], None))
    p._CACHE.clear()
    rows, warnings = p.fetch_pollster_rows(consultoras=["Opinaia"])
    # Se hicieron 2 búsquedas: una con recencia (tbs) y el fallback sin tbs.
    assert any(t for t in llamadas) and any(t is None for t in llamadas)
    assert len(rows) == 1 and rows[0]["candidato"] == "Javier Milei"
