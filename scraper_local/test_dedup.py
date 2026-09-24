"""Tests de dedup.py — puro, sin I/O."""
import dedup


def test_dedup_conserva_primera_aparicion():
    posts = [
        {"autor": "@user", "texto": "Hola mundo", "fecha": "2 h"},
        {"autor": "@user", "texto": "Hola mundo", "fecha": "3 h"},  # duplicado exacto
        {"autor": "@otra", "texto": "Hola mundo", "fecha": "4 h"},  # otro autor: queda
    ]
    out = dedup.dedup_posts(posts)
    assert len(out) == 2
    assert out[0]["fecha"] == "2 h"  # la primera aparición gana
    assert out[1]["autor"] == "@otra"


def test_dedup_normaliza_autor_y_texto():
    posts = [
        {"autor": "@User", "texto": "Hola   mundo"},
        {"autor": "user", "texto": "hola mundo"},       # sin @, case y espacios distintos
        {"autor": "@USER", "texto": "  HOLA MUNDO  "},
    ]
    assert len(dedup.dedup_posts(posts)) == 1


def test_dedup_texto_largo_compara_primeros_200():
    base = "x" * 300
    posts = [
        {"autor": "@a", "texto": base + "cola-1"},
        {"autor": "@a", "texto": base + "cola-2"},  # difieren después del char 200
    ]
    assert len(dedup.dedup_posts(posts)) == 1


def test_dedup_sin_identidad_no_colapsa():
    posts = [
        {"autor": "", "texto": ""},
        {"autor": "", "texto": ""},
    ]
    assert len(dedup.dedup_posts(posts)) == 2  # sin autor ni texto no hay identidad


def test_dedup_lista_vacia():
    assert dedup.dedup_posts([]) == []


def test_mismo_autor_y_texto_en_redes_distintas_no_colapsa():
    a = {"autor": "@user", "texto": "Vamos Milei", "red": "twitter"}
    b = {"autor": "@user", "texto": "Vamos Milei", "red": "tiktok"}
    assert len(dedup.dedup_posts([a, b])) == 2


def test_duplicado_en_la_misma_red_si_colapsa():
    a = {"autor": "@user", "texto": "Vamos Milei", "red": "tiktok"}
    assert dedup.dedup_posts([a, dict(a)]) == [a]
