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


def test_red_en_cero_por_cuota_culpa_a_gemini_no_al_scraping():
    v = alertas.evaluar(_base(posts_por_red={"twitter": 80, "tiktok": 0},
                              errores_cuota_por_red={"tiktok": 56}))
    assert v is not None
    assert "Gemini" in v["mensaje"] and "cuota" in v["mensaje"]
    assert "tiktok (captcha" not in v["mensaje"]  # NO culpar al scraping


def test_cero_sin_cuota_sigue_siendo_captcha():
    v = alertas.evaluar(_base(posts_por_red={"twitter": 80, "instagram": 0},
                              errores_cuota_por_red={}))
    assert v is not None
    assert "instagram (captcha / cuenta quemada)" in v["mensaje"]


def test_cuota_y_captcha_se_distinguen_en_la_misma_corrida():
    # tiktok cae por cuota de Gemini, instagram por scraping (sin errores de cuota)
    v = alertas.evaluar(_base(posts_por_red={"twitter": 80, "tiktok": 0, "instagram": 0},
                              errores_cuota_por_red={"tiktok": 56}))
    m = v["mensaje"]
    assert "Gemini" in m and "cuota" in m
    assert "instagram (captcha / cuenta quemada)" in m
    assert "tiktok (captcha" not in m


def test_errores_de_cuota_sin_red_en_cero_igual_alerta():
    # Aunque ninguna red quede en 0, si hubo capturas sin leer por cuota, avisa.
    v = alertas.evaluar(_base(posts_por_red={"twitter": 31}, warnings_total=2,
                              errores_cuota_por_red={"twitter": 27}))
    assert v is not None and "Gemini" in v["mensaje"]
