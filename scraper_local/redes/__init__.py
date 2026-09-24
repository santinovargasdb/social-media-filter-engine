"""Registro de redes del scraper local. Cada módulo cumple el contrato:

    capturar(sesion, termino, cfg, cfg_red, carpeta, prefijo, warnings)
        -> list[{"ruta": Path, "contexto": str}]     (SesionInvalidaError si muere)
    LOGIN_URL / login_completado(url)                (los usa accounts.py)
"""
from . import tiktok, twitter

POR_NOMBRE = {"twitter": twitter, "tiktok": tiktok}
