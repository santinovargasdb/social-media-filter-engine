"""Tests de redes/instagram.py — partes puras (selectores reales se tunean en vivo)."""
import redes
from redes import instagram


def test_registro_incluye_instagram():
    assert redes.POR_NOMBRE["instagram"] is instagram


def test_login_completado_fuera_de_login_y_challenge():
    assert instagram.login_completado("https://www.instagram.com/") is True
    assert instagram.login_completado("https://www.instagram.com/p/ABC123/") is True
    assert instagram.login_completado("https://www.instagram.com/explore/") is True
    assert instagram.login_completado("https://www.instagram.com/accounts/login/") is False
    assert instagram.login_completado("https://www.instagram.com/accounts/login/?next=/") is False
    assert instagram.login_completado("https://www.instagram.com/accounts/onetap/?next=/") is False
    assert instagram.login_completado("https://www.instagram.com/challenge/") is False
    assert instagram.login_completado("https://www.instagram.com/challenge/action/") is False


def test_links_de_posts_filtra_dedupea_y_corta():
    hrefs = [
        "https://www.instagram.com/p/AAA111/",
        "https://www.instagram.com/milfanpage_oficial/",   # perfil: afuera
        "https://www.instagram.com/p/AAA111/",             # repetido: afuera
        "https://www.instagram.com/p/BBB222/",
        "https://www.instagram.com/p/CCC333/",
    ]
    assert instagram.links_de_posts(hrefs, 2) == [
        "https://www.instagram.com/p/AAA111/",
        "https://www.instagram.com/p/BBB222/",
    ]


def test_links_de_posts_menos_que_pedidos():
    assert instagram.links_de_posts(["https://www.instagram.com/p/AAA/"], 5) == [
        "https://www.instagram.com/p/AAA/"
    ]
    assert instagram.links_de_posts([], 3) == []


def test_perfil_para_candidato_con_perfil_por_defecto():
    assert instagram._perfil_para("Javier Milei", {}) == "javiermilei"
    assert instagram._perfil_para("Axel Kicillof", {}) == "kicillofok"


def test_perfil_para_candidato_sin_perfil_es_none():
    assert instagram._perfil_para("Myriam Bregman", {}) is None


def test_perfil_para_override_de_config_pisa_default():
    cfg_red = {"perfiles_candidatos": {"Cristina Fernández de Kirchner": "cfkoficial"}}
    assert instagram._perfil_para("Cristina Fernández de Kirchner", cfg_red) == "cfkoficial"


def test_perfil_para_quita_arroba_y_espacios():
    cfg_red = {"perfiles_candidatos": {"Javier Milei": "  @MileiOficial  "}}
    assert instagram._perfil_para("Javier Milei", cfg_red) == "MileiOficial"
