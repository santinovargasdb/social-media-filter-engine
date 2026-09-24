"""
Cuentas descartables POR RED para el scraper local (`--red`, default twitter).

Pool en accounts.json (twitter, legacy) / accounts-<red>.json; las cookies
(storage_state de Playwright) viven en .sesiones/<alias>.json (twitter, legacy)
/ .sesiones/<red>-<alias>.json. El login es MANUAL una sola vez por cuenta —
sin passwords guardados:

    python accounts.py login <alias>            # X (twitter)
    python accounts.py login <alias> --red tiktok
    python accounts.py estado                   # lista el pool de twitter
    python accounts.py estado --red tiktok

Playwright se importa DIFERIDO (solo lo usa el CLI de login): la suite corre
sin playwright instalado.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import redes

BASE_DIR = Path(__file__).resolve().parent

LOGIN_TIMEOUT_MS = 300_000  # 5 min para loguear a mano


def ruta_pool(red: str = "twitter") -> Path:
    """twitter conserva accounts.json (legacy, pre multi-red); el resto va por red."""
    nombre = "accounts.json" if red == "twitter" else f"accounts-{red}.json"
    return BASE_DIR / nombre


def cargar_pool(ruta=None, red: str = "twitter") -> dict:
    ruta = Path(ruta) if ruta else ruta_pool(red)
    if not ruta.exists():
        return {"cuentas": []}
    return json.loads(ruta.read_text(encoding="utf-8"))


def guardar_pool(pool: dict, ruta=None, red: str = "twitter") -> None:
    ruta = Path(ruta) if ruta else ruta_pool(red)
    ruta.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def ruta_sesion(alias: str, red: str = "twitter") -> Path:
    """twitter conserva <alias>.json (las sesiones ya logueadas siguen valiendo)."""
    nombre = f"{alias}.json" if red == "twitter" else f"{red}-{alias}.json"
    return BASE_DIR / ".sesiones" / nombre


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


def _login(alias: str, red: str) -> int:
    """Login manual: navegador visible; se guarda solo al detectar que el login terminó
    (la detección es por red: X redirige a /home, TikTok sale de /login)."""
    from playwright.sync_api import sync_playwright  # diferido: solo el CLI lo necesita
    red_mod = redes.POR_NOMBRE[red]
    (BASE_DIR / ".sesiones").mkdir(exist_ok=True)
    pool = cargar_pool(red=red)
    if not any(c.get("alias") == alias for c in pool.get("cuentas", [])):
        pool.setdefault("cuentas", []).append(
            {"alias": alias, "estado": "activa", "ultima_vez": "", "notas": ""})
    with sync_playwright() as p:
        # Ventana maximizada, SIN viewport fijo y con escala 1:1. Sin esto,
        # Playwright fuerza 1280x720 adentro de la ventana real, y en pantallas
        # chicas con escalado de Windows (125%) el modal de login de X queda
        # recortado — el operador no puede completar los campos (medido: 488px
        # útiles de alto con escala 1.25 vs 633px con escala 1).
        # AutomationControlled apagado: con navigator.webdriver=true X deja
        # escribir el mail pero el botón "Siguiente" no responde (medido acá).
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized", "--force-device-scale-factor=1",
                  "--disable-blink-features=AutomationControlled"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(red_mod.LOGIN_URL)
        print(f"Logueá la cuenta '{alias}' de {red} en la ventana del navegador.")
        print("(Usá el login nativo con mail+contraseña, NO 'Continuar con Google'. "
              "Si algo queda recortado: scrolleá dentro del modal o achicá con Ctrl+menos.)")
        print("Cuando estés adentro se guarda solo (detecta que saliste del login).")
        try:
            page.wait_for_url(red_mod.login_completado, timeout=LOGIN_TIMEOUT_MS)
        except Exception:
            print(f"ERROR: no se detectó el login en {LOGIN_TIMEOUT_MS // 60000} min "
                  "(o se cerró la ventana). La cuenta NO se agregó; reintentá.")
            try:
                browser.close()
            except Exception:
                pass
            return 1
        context.storage_state(path=str(ruta_sesion(alias, red)))
        browser.close()
    for c in pool["cuentas"]:
        if c["alias"] == alias:
            c["estado"] = "activa"
    guardar_pool(pool, red=red)
    print(f"Sesión guardada en {ruta_sesion(alias, red)}. Cuenta '{alias}' activa.")
    return 0


def _estado(red: str) -> int:
    pool = cargar_pool(red=red)
    if not pool.get("cuentas"):
        print(f"Pool de {red} vacío. Agregá cuentas con: python accounts.py login <alias> --red {red}")
        return 0
    for c in pool["cuentas"]:
        sesion = "sesión OK" if ruta_sesion(c["alias"], red).exists() else "SIN sesión"
        print(f"- {c['alias']}: {c['estado']} · {sesion} · última vez: {c.get('ultima_vez') or 'nunca'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cuentas descartables del scraper (login manual, por red).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    login = sub.add_parser("login", help="login manual de una cuenta (navegador visible)")
    login.add_argument("alias")
    login.add_argument("--red", default="twitter", choices=sorted(redes.POR_NOMBRE))
    estado = sub.add_parser("estado", help="lista el pool")
    estado.add_argument("--red", default="twitter", choices=sorted(redes.POR_NOMBRE))
    args = ap.parse_args(argv)
    if args.cmd == "login":
        return _login(args.alias, args.red)
    return _estado(args.red)


if __name__ == "__main__":
    sys.exit(main())
