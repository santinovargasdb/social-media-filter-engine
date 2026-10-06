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


# Casi-duplicados de la MISMA cuenta: el mismo contenido reposteado con otra
# reacción pegada adelante (ej. una risa). Difieren al principio pero comparten
# casi todas las palabras → se colapsan por similitud, no por contención exacta.
_CITA = ('KICILLOF: "TANTO ESTADOS UNIDOS, COMO EUROPA... Y LOS DEMAS '
         'PLANETAS" Y ESTE TIPO ES GOBERANDOR')


def test_dedup_casi_duplicado_misma_cuenta_colapsa():
    posts = [
        {"autor": "@cuenta", "texto": _CITA, "red": "instagram"},
        {"autor": "@cuenta", "texto": "JAJDJAJDAJDJAJADJAJJAJSJFJAJAA " + _CITA,
         "red": "instagram"},  # misma cita + risa adelante
    ]
    out = dedup.dedup_posts(posts)
    assert len(out) == 1
    assert out[0]["texto"] == _CITA  # gana la primera aparición


def test_dedup_casi_duplicado_cuentas_distintas_no_colapsa():
    # Dos personas distintas citando lo mismo son publicaciones legítimas: no se tocan.
    posts = [
        {"autor": "@uno", "texto": _CITA, "red": "instagram"},
        {"autor": "@dos", "texto": "jaja " + _CITA, "red": "instagram"},
    ]
    assert len(dedup.dedup_posts(posts)) == 2


def test_dedup_texto_corto_distinto_no_colapsa():
    # Textos cortos (ej. tiktok truncados) de la misma cuenta: aunque compartan
    # alguna palabra, por debajo del piso de tokens no se tratan como duplicado.
    posts = [
        {"autor": "@a", "texto": "Milei", "red": "x"},
        {"autor": "@a", "texto": "Milei presidente de todos los argentinos", "red": "x"},
    ]
    assert len(dedup.dedup_posts(posts)) == 2


# --- Casos REALES del snapshot 2026-10-05 (traído del backend en vivo) ---

def test_dedup_real_hombregrisxd_colapsa():
    # Misma cuenta, mismo clip de Kicillof con distinta reacción adelante
    # ("...planetas" ajajaja  vs  JAJDJAJD...). Jaccard de palabras = 0.875.
    a = ('“Estados Unidos Europa y los demás planetas” ajajajajajjajajajaja\n'
         'KICILLOF: "TANTO ESTADOS UNIDOS, COMO EUROPA... Y LOS DEMAS PLANETAS"\n'
         'Y ESTE TIPO ES GOBERANDOR')
    b = ('JAJDJAJDAJDJAJADJAJJAJSJFJAJAA\n\n'
         'KICILLOF: "TANTO ESTADOS UNIDOS, COMO EUROPA... Y LOS DEMAS PLANETAS"\n\n'
         'Y ESTE TIPO ES GOBERANDOR')
    posts = [
        {"autor": "@hombregrisxd", "texto": a, "red": "instagram"},
        {"autor": "@hombregrisxd", "texto": b, "red": "instagram"},
    ]
    assert len(dedup.dedup_posts(posts)) == 1


def test_dedup_real_macxiale_no_colapsa():
    # Misma cuenta, DOS tuits distintos que solo comparten el chiste "(Schiaretti)".
    # Jaccard de palabras = 0.19 → publicaciones diferentes, no se tocan.
    t1 = ("Y, si no podemos votar a Cristina, el soberano decidirá. Hasta las "
          "elecciones, pululan Axel, Sergio, Juan (no, vos no Schiaretti).\n\n"
          "Habría que arrobarlos para que se enteren cómo habla la gente. "
          "Escuchando sale candidato.")
    t2 = ("Una mirada federal que supere la Av. Gral. Paz y que no se quede en "
          "PBA.\n\nNo, vos no Juan (Schiaretti).\n\nEntonces, superar los '70 y la "
          "mirada federal lleva a... ¡Qué hacés Natalia de la Sota?")
    posts = [
        {"autor": "@macxiale", "texto": t1, "red": "twitter"},
        {"autor": "@macxiale", "texto": t2, "red": "twitter"},
    ]
    assert len(dedup.dedup_posts(posts)) == 2
