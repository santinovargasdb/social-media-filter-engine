"""
Orquestador del scraper local — lo dispara Task Scheduler 2-3×/día.

Por red × candidato: capturas (browser/redes, rotando cuentas si se queman) →
lectura con Gemini visión → dedup por red → un bloque por red → agregación
REUSANDO la lógica del backend (electoral._merge_bloques fusiona y calcula
por_red) → snapshot a Supabase.

Regla de seguridad: si la corrida no junta `min_posts_electorales` posts
electorales, o la subida falla, NO se pisa el snapshot anterior y el exit code
es 1 (Task Scheduler lo registra como fallo).

Uso:  python run.py [--dry-run] [--config ruta] [--redes twitter,tiktok]
"""
import argparse
import json
import logging
import os
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# El scraper corre desde el repo clonado en la PC de la oficina: reusa el código
# del backend agregándolo al path (mismo patrón que vision.py).
BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = str(BASE_DIR.parent / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import accounts  # noqa: E402
import browser  # noqa: E402
import dedup  # noqa: E402
import redes  # noqa: E402
import vision  # noqa: E402
import electoral  # noqa: E402
import store  # noqa: E402

RUTA_CONFIG = BASE_DIR / "config.json"
DIR_CAPTURAS = BASE_DIR / "capturas"
DIR_LOGS = BASE_DIR / "logs"
LOCK_PATH = BASE_DIR / ".run.lock"

log = logging.getLogger("urna.scraper")


class InstanciaActivaError(Exception):
    """Ya hay otra corrida del scraper en curso (el lock está tomado)."""


def _tomar_lock_os(fh) -> None:
    """Lock exclusivo NO bloqueante sobre el file handle; lanza OSError si ya está
    tomado por otra corrida. El lock lo mantiene el SO y se libera solo cuando el
    proceso muere (incluso por crash/OOM) — así nunca queda un lock obsoleto."""
    if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _soltar_lock_os(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def lock_de_instancia(ruta: Path = LOCK_PATH):
    """Impide DOS corridas del scraper en paralelo. Corridas simultáneas colisionan
    de dos formas medidas en prod (2026-09-30): (1) race en `accounts.json` — una
    marca una cuenta y la otra la re-escribe → "Sin cuentas activas" espurio; (2)
    RAM — dos Chromium en la PC de 8 GB agotan la memoria y una corrida se cae por
    OOM. Lanza InstanciaActivaError si ya hay otra activa."""
    fh = open(ruta, "a+")
    try:
        _tomar_lock_os(fh)
    except OSError as e:
        fh.close()
        raise InstanciaActivaError(f"otra corrida activa (lock {ruta})") from e
    try:
        yield
    finally:
        _soltar_lock_os(fh)
        fh.close()

CONFIG_DEFAULT = {
    "redes": {"twitter": {"scrolls_por_candidato": 3}},
    "candidatos": None,  # None = electoral.CANDIDATOS_DEFAULT
    "espera_entre_scrolls": [2, 5],
    "espera_entre_candidatos": [20, 40],
    "viewport": [950, 1300],
    "headless": True,
    "min_posts_electorales": 1,
    "conservar_corridas": 3,
}


def cargar_config(ruta=None) -> dict:
    cfg = dict(CONFIG_DEFAULT)
    ruta = Path(ruta) if ruta else RUTA_CONFIG
    if ruta.exists():
        cfg.update(json.loads(ruta.read_text(encoding="utf-8")))
    # Formato legacy (Fase 3): "red" + "scrolls_por_candidato" planos.
    if "red" in cfg:
        cfg["redes"] = {cfg.pop("red"): {
            "scrolls_por_candidato": cfg.pop("scrolls_por_candidato", 3)}}
    return cfg


def filtrar_redes(cfg: dict, redes_csv: str | None) -> dict:
    """Acota cfg["redes"] a las de --redes (coma-separado). Aborta si no queda ninguna."""
    if redes_csv:
        pedidas = {r.strip() for r in redes_csv.split(",") if r.strip()}
        cfg["redes"] = {k: v for k, v in cfg["redes"].items() if k in pedidas}
    if not cfg["redes"]:
        msg = (
            "config sin redes activas." if redes_csv is None
            else f"--redes '{redes_csv}': ninguna red del config coincide."
        )
        sys.exit(msg)
    return cfg


def _author_url(autor: str, red: str) -> str:
    if not autor.startswith("@"):
        return ""
    if red == "twitter":
        return f"https://x.com/{autor[1:]}"
    if red == "tiktok":
        return f"https://www.tiktok.com/{autor}"
    if red == "instagram":
        return f"https://www.instagram.com/{autor[1:]}/"
    return ""


def _slug(nombre: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in nombre.lower()).strip("-")


def armar_bloque(posts: list[dict], red: str, busquedas: int, crudos: int, errores: int) -> dict:
    """Mapea los posts de visión (ya deduplicados) al shape de bloque de electoral.
    En visión extraer y clasificar es UNA pasada, así que analizados == encontrados.
    Los IDs usan f"{red}_{i}" para evitar colisiones al fusionar bloques de distintas redes."""
    posts_by_id: dict[str, dict] = {}
    analysis: list[dict] = []
    for i, post in enumerate(posts):
        pid = f"{red}_{i}"
        autor = post.get("autor") or ""
        posts_by_id[pid] = {
            "network": red, "author": autor, "author_url": _author_url(autor, red),
            "text": post.get("texto") or "", "post_url": "", "date": post.get("fecha") or "",
        }
        analysis.append({
            "id": pid,
            "candidatos": post.get("candidatos") or [],
            "cita": post.get("cita") or "",
            "es_electoral": bool(post.get("es_electoral", False)),
        })
    status = {"red": red, "busquedas": busquedas, "crudos": crudos, "errores": errores,
              "encontrados": len(posts), "analizados": len(posts)}
    return {"network": red, "posts_by_id": posts_by_id, "analysis": analysis, "status": status}


def armar_payload(bloques: list[dict], warnings: list[str]) -> dict:
    """Arma el payload del snapshot con la MISMA forma que electoral.run_boca_de_urna."""
    candidatos, baja_conf, posts_by_id, analysis = electoral._merge_bloques(bloques)
    evidencia = electoral.build_evidence(analysis, posts_by_id)
    comparacion, comp_warnings = electoral.compare_vs_pollsters(candidatos, [])
    warnings = list(warnings) + list(comp_warnings)
    if baja_conf:
        warnings.append(
            f"{baja_conf} mención(es) descartada(s) por baja confianza (< {electoral.CONF_MIN}).")
    posts_electorales = sum(1 for a in analysis if a.get("es_electoral"))
    return {
        "candidatos": candidatos, "evidencia": evidencia, "comparacion": comparacion,
        "meta": {"total_posts": sum(b["status"]["encontrados"] for b in bloques),
                 "posts_electorales": posts_electorales,
                 "analizados": sum(b["status"]["analizados"] for b in bloques),
                 "bloques": [b["status"] for b in bloques],
                 "disclaimer": electoral.DISCLAIMER, "warnings": warnings},
    }


def capturar_candidato(red_nombre: str, cfg: dict, pool: dict, candidato: str,
                       carpeta: Path, warnings: list[str]) -> list[dict]:
    """Capturas de UN candidato en UNA red, rotando la cuenta UNA vez si se quema.
    Solo el redirect a login (SesionInvalidaError) quema; captcha/timeout
    (DesafioTemporalError) saltea el candidato sin tocar la cuenta.
    Devuelve [] si no se pudo (el resto de la corrida sigue)."""
    red_mod = redes.POR_NOMBRE[red_nombre]
    for _intento in range(2):  # cuenta actual + una rotación
        cuenta = accounts.proxima_cuenta(pool)
        if cuenta is None:
            warnings.append(f"[{red_nombre}] Sin cuentas activas: '{candidato}' quedó sin capturar.")
            return []
        try:
            capturas = red_mod.capturar(
                sesion=accounts.ruta_sesion(cuenta["alias"], red_nombre), termino=candidato,
                cfg=cfg, cfg_red=cfg["redes"][red_nombre], carpeta=carpeta,
                prefijo=f"{red_nombre}-{_slug(candidato)}", warnings=warnings)
            accounts.registrar_uso(pool, cuenta["alias"])
            return capturas
        except browser.DesafioTemporalError as e:
            # Challenge a la máquina/IP, no a la cuenta: no se quema ni se rota
            # (la otra cuenta chocaría con el mismo muro desde esta misma IP).
            log.warning("[%s] Challenge temporal con '%s': %s", red_nombre, candidato, e)
            warnings.append(f"[{red_nombre}] '{candidato}' salteado por challenge temporal "
                            f"({e}); la cuenta sigue activa.")
            return []
        except browser.SesionInvalidaError as e:
            log.warning("[%s] Cuenta '%s' quemada/challenge: %s", red_nombre, cuenta["alias"], e)
            accounts.marcar_quemada(pool, cuenta["alias"])
            warnings.append(f"[{red_nombre}] Cuenta '{cuenta['alias']}' marcada como quemada.")
        except Exception as e:
            # Error inesperado del browser (Playwright caído, sesión ilegible, etc.):
            # no es evidencia de cuenta quemada — se registra y la corrida sigue.
            log.error("[%s] Error inesperado capturando '%s': %s", red_nombre, candidato, e)
            warnings.append(f"[{red_nombre}] '{candidato}' quedó sin capturar (error del navegador: {e}).")
            return []
    warnings.append(f"[{red_nombre}] '{candidato}' quedó sin capturar (dos cuentas fallaron).")
    return []


def _limpiar_corridas_viejas(conservar: int) -> None:
    """Borra las carpetas de capturas más viejas, conservando las últimas N."""
    if conservar <= 0 or not DIR_CAPTURAS.exists():
        return
    corridas = sorted((d for d in DIR_CAPTURAS.iterdir() if d.is_dir()), key=lambda d: d.name)
    for vieja in corridas[:-conservar]:
        shutil.rmtree(vieja, ignore_errors=True)


def correr(cfg: dict, dry_run: bool = False) -> int:
    inicio = datetime.now(timezone.utc)
    carpeta = DIR_CAPTURAS / inicio.strftime("%Y%m%d-%H%M")
    warnings: list[str] = []
    candidatos = cfg.get("candidatos") or electoral.CANDIDATOS_DEFAULT

    bloques: list[dict] = []
    for red_nombre in cfg["redes"]:
        if red_nombre not in redes.POR_NOMBRE:
            warnings.append(f"Red desconocida en config: '{red_nombre}' (se saltea).")
            continue
        pool = accounts.cargar_pool(red=red_nombre)
        crudos_red: list[dict] = []
        errores = 0
        for i, candidato in enumerate(candidatos):
            log.info("[%s] Candidato %d/%d: %s", red_nombre, i + 1, len(candidatos), candidato)
            capturas = capturar_candidato(red_nombre, cfg, pool, candidato, carpeta, warnings)
            for cap in capturas:
                leidos = vision.read_capture(cap["ruta"], red_nombre, candidatos,
                                             contexto=cap["contexto"])
                if leidos is None:
                    errores += 1
                    warnings.append(f"Lectura fallida (Gemini) de {cap['ruta'].name}.")
                    continue
                crudos_red.extend(leidos)
            if i + 1 < len(candidatos) and capturas:
                browser.esperar_aleatorio(tuple(cfg["espera_entre_candidatos"]))
        accounts.guardar_pool(pool, red=red_nombre)
        posts_red = dedup.dedup_posts(crudos_red)
        log.info("[%s] Posts: %d crudos, %d tras dedup, %d errores de lectura.",
                 red_nombre, len(crudos_red), len(posts_red), errores)
        bloques.append(armar_bloque(posts_red, red_nombre, busquedas=len(candidatos),
                                    crudos=len(crudos_red), errores=errores))

    payload = armar_payload(bloques, warnings)
    pe = payload["meta"]["posts_electorales"]

    if dry_run:
        log.info("[dry-run] NO se sube el snapshot.")
        print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))
        return 0
    if pe < cfg["min_posts_electorales"]:
        log.error("Solo %d post(s) electoral(es) (mínimo %d): NO se pisa el snapshot anterior.",
                  pe, cfg["min_posts_electorales"])
        return 1
    if not store.write_snapshot(payload, inicio.isoformat()):
        log.error("La subida a Supabase falló: el snapshot anterior queda vigente.")
        return 1
    log.info("Snapshot subido: %d candidatos, %d posts electorales, %d warnings.",
             len(payload["candidatos"]), pe, len(payload["meta"]["warnings"]))
    _limpiar_corridas_viejas(cfg["conservar_corridas"])
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Corrida del scraper local de la Boca de Urna.")
    ap.add_argument("--dry-run", action="store_true",
                    help="no sube el snapshot ni limpia capturas; imprime el meta")
    ap.add_argument("--config", default=None, help="ruta alternativa de config.json")
    ap.add_argument("--redes", default=None,
                    help="coma-separado (ej. twitter,tiktok); acota la corrida a esas redes")
    args = ap.parse_args(argv)
    DIR_LOGS.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(
                      DIR_LOGS / f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.log",
                      encoding="utf-8")])
    cfg = cargar_config(args.config)
    cfg = filtrar_redes(cfg, args.redes)
    try:
        with lock_de_instancia(LOCK_PATH):
            return correr(cfg, dry_run=args.dry_run)
    except InstanciaActivaError as e:
        log.error("No arranco: %s. Ya hay una corrida en curso; evito colisionar "
                  "por las cuentas y la RAM.", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
