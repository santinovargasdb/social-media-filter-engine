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


def test_hashtags_para_no_mapeado_con_enie_agrega_variante_ascii():
    assert instagram._hashtags_para("Sergio Uñac", {}) == ["SergioUñac", "SergioUnac"]


def test_hashtags_para_candidato_mapeado_ascii_sin_variante():
    assert instagram._hashtags_para("Cristina Fernández de Kirchner", {}) == ["cfk"]


def test_hashtags_para_nombre_ascii_sin_variante():
    assert instagram._hashtags_para("Javier Milei", {}) == ["javierMilei"]


def test_hashtags_para_override_no_ascii_agrega_variante_ascii():
    cfg_red = {"hashtags_candidatos": {"Sergio Uñac": "uñacGobernador"}}
    assert instagram._hashtags_para("Sergio Uñac", cfg_red) == [
        "uñacGobernador", "unacGobernador"
    ]


def test_hashtags_para_dedup_si_plegado_igual_al_primario():
    cfg_red = {"hashtags_candidatos": {"Sergio Uñac": "sergiounac"}}
    assert instagram._hashtags_para("Sergio Uñac", cfg_red) == ["sergiounac"]
