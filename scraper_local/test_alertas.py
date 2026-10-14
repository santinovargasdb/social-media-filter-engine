"""Tests de alertas.evaluar — lógica pura, sin I/O."""
import alertas


def _base(**over):
    r = {"exit_code": 0,
         "posts_por_red": {"twitter": 100, "tiktok": 300, "instagram": 40},
         "warnings_total": 2,
         "cuentas_quemadas": []}
    r.update(over)
    return r


def test_corrida_sana_no_alerta():
    assert alertas.evaluar(_base()) is None


def test_red_en_cero_alerta():
    v = alertas.evaluar(_base(posts_por_red={"twitter": 79, "tiktok": 0, "instagram": 38}))
    assert v is not None and v["nivel"] == "alerta"
    assert "tiktok" in v["mensaje"]
    assert v["titulo"] == alertas.TITULO


def test_muchos_avisos_alerta():
    v = alertas.evaluar(_base(warnings_total=23))
    assert v is not None and v["nivel"] == "alerta"
    assert "23 avisos" in v["mensaje"]


def test_exit_code_1_alerta():
    assert alertas.evaluar(_base(exit_code=1)) is not None


def test_lock_exit_code_2_no_alerta():
    assert alertas.evaluar(_base(exit_code=2)) is None


def test_cuenta_quemada_alerta():
    v = alertas.evaluar(_base(cuentas_quemadas=["[tiktok] Cuenta 'tt1' marcada como quemada."]))
    assert v is not None


def test_umbral_configurable():
    assert alertas.evaluar(_base(warnings_total=5), umbral_warnings=4) is not None
    assert alertas.evaluar(_base(warnings_total=5), umbral_warnings=8) is None


def test_solo_twitter_con_posts_no_alerta():
    assert alertas.evaluar(_base(posts_por_red={"twitter": 119})) is None


def test_solo_twitter_en_cero_alerta():
    assert alertas.evaluar(_base(posts_por_red={"twitter": 0})) is not None


def test_varios_motivos_se_concatenan():
    v = alertas.evaluar(_base(exit_code=1, warnings_total=30,
                              posts_por_red={"twitter": 0}))
    # tres motivos distintos concatenados: fallo de tablero, red en 0 y avisos
    assert "No se pudo actualizar el tablero" in v["mensaje"]
    assert "twitter" in v["mensaje"]
    assert "30 avisos" in v["mensaje"]
