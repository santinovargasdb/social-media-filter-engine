"""Decide si una corrida del scraper merece un aviso. Lógica pura, sin I/O ni UI.

El resultado lo arma run.py y lo consume aviso-matutino.ps1; acá sólo se aplica
la regla de "cuándo es alerta".
"""

TITULO = "⚠️ Boca de Urna: corrida con problemas"


def evaluar(resultado: dict, umbral_warnings: int = 8) -> dict | None:
    """Devuelve {'nivel','titulo','mensaje'} si la corrida merece alerta, o None.

    Es alerta si se cumple CUALQUIERA:
      1. exit_code no es 0 ni 2  (crash / pocos posts / subida fallida; 2 = lock, normal)
      2. alguna red corrida trajo 0 posts
      3. hubo al menos una cuenta quemada
      4. los avisos totales llegan al umbral
    """
    motivos: list[str] = []

    if resultado.get("exit_code") not in (0, 2):
        motivos.append("No se pudo actualizar el tablero (la corrida falló).")

    ceros = [red for red, n in (resultado.get("posts_por_red") or {}).items() if n == 0]
    if ceros:
        motivos.append(f"Sin posts en: {', '.join(ceros)} (captcha / cuenta quemada).")

    quemadas = resultado.get("cuentas_quemadas") or []
    if quemadas:
        motivos.append(f"{len(quemadas)} cuenta(s) quemada(s) en esta corrida.")

    total_avisos = resultado.get("warnings_total") or 0
    if total_avisos >= umbral_warnings:
        motivos.append(f"{total_avisos} avisos en la corrida.")

    if not motivos:
        return None
    return {"nivel": "alerta", "titulo": TITULO, "mensaje": " ".join(motivos)}
