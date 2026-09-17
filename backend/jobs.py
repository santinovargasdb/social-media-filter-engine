"""
Almacén EN MEMORIA de trabajos async de la Boca de Urna.

Sin base de datos: un dict protegido con un Lock, con TTL. Alcanza para el free-tier
de Render (un worker; se duerme por inactividad, pero mientras el frontend consulta
el estado se mantiene despierto y el trabajo termina). Un trabajo nace en `running`,
reporta progreso, y termina en `done` (con resultado) o `error` (con mensaje).

Si la instancia se reinicia, los trabajos en curso se pierden: el caller debe tratar
un job_id inexistente como "reintentá".
"""
import threading
import time
import uuid

# Los trabajos viejos se barren al crear uno nuevo (evita crecer sin límite).
_TTL = 30 * 60  # 30 minutos
_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}


def _sweep(now: float) -> None:
    """Elimina trabajos vencidos. Debe llamarse con _LOCK tomado."""
    for jid in [j for j, v in _JOBS.items() if now - v["updated"] >= _TTL]:
        del _JOBS[jid]


def create() -> str:
    """Crea un trabajo en estado `running` y devuelve su id."""
    jid = uuid.uuid4().hex
    now = time.time()
    with _LOCK:
        _sweep(now)
        _JOBS[jid] = {
            "state": "running",
            "progress": {"phase": "En cola…", "pct": 0.0},
            "result": None,
            "error": None,
            "updated": now,
        }
    return jid


def set_progress(job_id: str, phase: str, pct: float) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is not None:
            j["progress"] = {"phase": phase, "pct": round(pct, 1)}
            j["updated"] = time.time()


def set_result(job_id: str, result: dict) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is not None:
            j["state"] = "done"
            j["result"] = result
            j["progress"] = {"phase": "Listo", "pct": 100.0}
            j["updated"] = time.time()


def set_error(job_id: str, message: str) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if j is not None:
            j["state"] = "error"
            j["error"] = message
            j["updated"] = time.time()


def get(job_id: str) -> dict | None:
    """Devuelve una COPIA del trabajo (para no exponer el dict interno mutable)."""
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j is not None else None
