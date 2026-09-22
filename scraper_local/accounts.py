"""
Cuentas descartables de X para el scraper local (Fase 3).

Pool en accounts.json (GIT-IGNORED) con estado por cuenta; las cookies
(storage_state de Playwright) viven en .sesiones/<alias>.json (GIT-IGNORED).
El login es MANUAL una sola vez por cuenta — sin passwords guardados:

    python accounts.py login <alias>   # navegador visible, logueás a mano
    python accounts.py estado          # lista el pool

Playwright se importa DIFERIDO (solo lo usa el CLI de login): la suite corre
sin playwright instalado.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUTA_POOL = BASE_DIR / "accounts.json"
DIR_SESIONES = BASE_DIR / ".sesiones"

X_LOGIN_URL = "https://x.com/login"


def cargar_pool(ruta=None) -> dict:
    ruta = Path(ruta) if ruta else RUTA_POOL
    if not ruta.exists():
        return {"cuentas": []}
    return json.loads(ruta.read_text(encoding="utf-8"))


def guardar_pool(pool: dict, ruta=None) -> None:
    ruta = Path(ruta) if ruta else RUTA_POOL
    ruta.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def proxima_cuenta(pool: dict) -> dict | None:
    """La cuenta activa usada hace más tiempo ('' = nunca usada, va primero)."""
    activas = [c for c in pool.get("cuentas", []) if c.get("estado") == "activa"]
    if not activas:
        return None
    return sorted(activas, key=lambda c: c.get("ultima_vez") or "")[0]


def registrar_uso(pool: dict, alias: str) -> None:
    for c in pool.get("cuentas", []):
        if c.get("alias") == alias:
            c["ultima_vez"] = datetime.now(timezone.utc).isoformat()


def marcar_quemada(pool: dict, alias: str) -> None:
    for c in pool.get("cuentas", []):
        if c.get("alias") == alias:
            c["estado"] = "quemada"


def ruta_sesion(alias: str) -> Path:
    return DIR_SESIONES / f"{alias}.json"


def _login(alias: str) -> int:
    """Login manual: navegador visible; el operador loguea y aprieta Enter acá."""
    from playwright.sync_api import sync_playwright  # diferido: solo el CLI lo necesita
    DIR_SESIONES.mkdir(exist_ok=True)
    pool = cargar_pool()
    if not any(c.get("alias") == alias for c in pool.get("cuentas", [])):
        pool.setdefault("cuentas", []).append(
            {"alias": alias, "estado": "activa", "ultima_vez": "", "notas": ""})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(X_LOGIN_URL)
        print(f"Logueá la cuenta '{alias}' en la ventana del navegador.")
        input("Cuando estés adentro (se ve el timeline), apretá Enter acá... ")
        context.storage_state(path=str(ruta_sesion(alias)))
        browser.close()
    for c in pool["cuentas"]:
        if c["alias"] == alias:
            c["estado"] = "activa"
    guardar_pool(pool)
    print(f"Sesión guardada en {ruta_sesion(alias)}. Cuenta '{alias}' activa.")
    return 0


def _estado() -> int:
    pool = cargar_pool()
    if not pool.get("cuentas"):
        print("Pool vacío. Agregá cuentas con: python accounts.py login <alias>")
        return 0
    for c in pool["cuentas"]:
        sesion = "sesión OK" if ruta_sesion(c["alias"]).exists() else "SIN sesión"
        print(f"- {c['alias']}: {c['estado']} · {sesion} · última vez: {c.get('ultima_vez') or 'nunca'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cuentas descartables del scraper (login manual).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    login = sub.add_parser("login", help="login manual de una cuenta (navegador visible)")
    login.add_argument("alias")
    sub.add_parser("estado", help="lista el pool")
    args = ap.parse_args(argv)
    if args.cmd == "login":
        return _login(args.alias)
    return _estado()


if __name__ == "__main__":
    sys.exit(main())
