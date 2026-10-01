"""Tests de compare_vs_pollsters — compara SHARE DE APOYO (no share de menciones):
las consultoras miden intención de voto (reparte 100% entre candidatos); el share
de menciones mide volumen de conversación y daba gaps enormes sin sentido."""
import electoral


def _cand(nombre, pos, pct):
    return {"nombre": nombre, "pct": pct, "pos": pos, "neg": 1, "neu": 1,
            "menciones": pos + 2, "pos_pct": 50, "neg_pct": 25, "neu_pct": 25}


FILA_MILEI_38 = [{"consultora": "Analía", "fecha": "2026-09-20",
                  "candidato": "Javier Milei", "porcentaje": 38.0}]


def test_compara_contra_share_de_apoyo():
    # Milei 3 de 4 positivas del corpus = 75% de apoyo (su share de menciones, 50%,
    # NO se usa). Gap contra la consultora: 75 - 38 = +37.
    candidatos = [_cand("Javier Milei", pos=3, pct=50.0), _cand("Axel Kicillof", pos=1, pct=50.0)]
    comparacion, _ = electoral.compare_vs_pollsters(candidatos, FILA_MILEI_38)
    milei = next(c for c in comparacion if c["candidato"] == "Javier Milei")
    assert milei["redes_pct"] == 75.0
    assert milei["redes_metrica"] == "apoyo"
    assert milei["consultoras"][0]["gap"] == 37.0
    assert milei["gap_promedio"] == 37.0
    kici = next(c for c in comparacion if c["candidato"] == "Axel Kicillof")
    assert kici["redes_pct"] == 25.0


def test_sin_positivas_cae_a_share_de_menciones_y_avisa():
    candidatos = [_cand("Javier Milei", pos=0, pct=40.0)]
    comparacion, warnings = electoral.compare_vs_pollsters(candidatos, FILA_MILEI_38)
    assert comparacion[0]["redes_pct"] == 40.0
    assert comparacion[0]["redes_metrica"] == "menciones"
    assert any("menciones" in w for w in warnings)


def test_sin_consultoras_no_agrega_warning_de_fallback():
    # El scraper llama con pollster_rows=[] para armar cada snapshot: un corpus sin
    # positivas no debe ensuciar TODOS los snapshots con el aviso del fallback.
    candidatos = [_cand("Javier Milei", pos=0, pct=40.0)]
    _, warnings = electoral.compare_vs_pollsters(candidatos, [])
    assert warnings == []
