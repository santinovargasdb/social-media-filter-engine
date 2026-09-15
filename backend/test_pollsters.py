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
