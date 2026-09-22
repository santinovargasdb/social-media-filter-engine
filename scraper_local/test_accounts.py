"""Tests de accounts.py — pool y rotación, sin browser (el login manual no se testea)."""
from datetime import datetime

import accounts


def _pool(*cuentas):
    return {"cuentas": [dict(c) for c in cuentas]}


A1 = {"alias": "a1", "estado": "activa", "ultima_vez": "2026-09-20T10:00:00+00:00", "notas": ""}
A2 = {"alias": "a2", "estado": "activa", "ultima_vez": "2026-09-21T10:00:00+00:00", "notas": ""}
NUEVA = {"alias": "n1", "estado": "activa", "ultima_vez": "", "notas": ""}
QUEMADA = {"alias": "q1", "estado": "quemada", "ultima_vez": "", "notas": ""}


def test_cargar_pool_inexistente_devuelve_vacio(tmp_path):
    assert accounts.cargar_pool(tmp_path / "no-existe.json") == {"cuentas": []}


def test_roundtrip_guardar_cargar(tmp_path):
    ruta = tmp_path / "pool.json"
    pool = _pool(A1, QUEMADA)
    accounts.guardar_pool(pool, ruta)
    assert accounts.cargar_pool(ruta) == pool


def test_proxima_cuenta_prefiere_nunca_usada_y_mas_vieja():
    assert accounts.proxima_cuenta(_pool(A2, A1, NUEVA))["alias"] == "n1"
    assert accounts.proxima_cuenta(_pool(A2, A1))["alias"] == "a1"


def test_proxima_cuenta_excluye_quemadas_y_pool_agotado():
    assert accounts.proxima_cuenta(_pool(QUEMADA)) is None
    assert accounts.proxima_cuenta(_pool()) is None


def test_marcar_quemada_y_registrar_uso():
    pool = _pool(A1, A2)
    accounts.marcar_quemada(pool, "a1")
    assert pool["cuentas"][0]["estado"] == "quemada"
    accounts.registrar_uso(pool, "a2")
    # timestamp ISO parseable y no vacío
    assert datetime.fromisoformat(pool["cuentas"][1]["ultima_vez"])


def test_ruta_sesion():
    ruta = accounts.ruta_sesion("cuenta1")
    assert ruta.name == "cuenta1.json"
    assert ruta.parent.name == ".sesiones"
