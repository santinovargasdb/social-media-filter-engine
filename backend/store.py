"""
Almacén persistente del snapshot de la Boca de Urna (Supabase).

Flujo del modo `stored`: el scraper local (batch, en la PC de la oficina) escribe UN
snapshot con `write_snapshot()`; el backend en Render lo lee con `read_latest_snapshot()`
y lo sirve tal cual. Así la app muestra el último análisis real aunque la PC esté
apagada. Solo usa `requests` (nada de SDK). Config por variables de entorno:

- SUPABASE_URL: URL del proyecto (ej. https://abcd.supabase.co)
- SUPABASE_KEY: API key (para leer desde Render alcanza la anon key con RLS de lectura;
  el scraper que escribe usa la service key)
- URNA_SNAPSHOT_TABLE: nombre de la tabla (default 'urna_snapshot')

El snapshot (`payload`) tiene la MISMA forma que devuelve `electoral.run_boca_de_urna`
(candidatos / evidencia / comparacion / meta), así el frontend no cambia.
"""
import os
from datetime import datetime, timedelta, timezone

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
_TABLE = os.environ.get("URNA_SNAPSHOT_TABLE", "urna_snapshot")
_TIMEOUT = 15

# Los `generado_en` se guardan en UTC pero el usuario piensa en día argentino
# (UTC-3, sin horario de verano desde 2009).
TZ_ARGENTINA = timezone(timedelta(hours=-3))


def _headers() -> dict:
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def _leer_filas(params: dict) -> list | None:
    """GET a la tabla de snapshots. None si no está configurado o falló la lectura."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("DEBUG store: Supabase no configurado (SUPABASE_URL/SUPABASE_KEY).")
        return None
    try:
        resp = requests.get(f"{SUPABASE_URL}/rest/v1/{_TABLE}", params=params,
                            headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"ERROR store.read: {e}")
        return None


def _payload_de(row: dict) -> dict | None:
    payload = row.get("payload") or None
    if isinstance(payload, dict):
        payload.setdefault("meta", {})["ultima_actualizacion"] = row.get("generado_en")
    return payload


def read_latest_snapshot() -> dict | None:
    """Devuelve el último snapshot guardado (dict con la forma del resultado de la urna,
    con `meta.ultima_actualizacion` inyectada), o None si no hay / no está configurado /
    falló la lectura."""
    rows = _leer_filas({"select": "payload,generado_en",
                        "order": "generado_en.desc", "limit": 1})
    if not rows:
        return None
    return _payload_de(rows[0])


def _es_anterior_a(generado_en, limite: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(generado_en).replace("Z", "+00:00")) < limite
    except ValueError:
        return False


def read_snapshot_for_date(fecha: str) -> dict | None:
    """Último snapshot del día `fecha` ('YYYY-MM-DD', día argentino). Si ese día no
    tuvo corrida devuelve el más cercano ANTERIOR avisando en meta.warnings (la UI
    ya muestra la fecha real vía ultima_actualizacion). None si no hay ninguno hasta
    esa fecha / sin config / error de lectura. ValueError si la fecha no parsea."""
    try:
        dia = datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=TZ_ARGENTINA)
    except ValueError:
        raise ValueError(f"Fecha inválida: '{fecha}' (se espera AAAA-MM-DD).")
    ini = dia.astimezone(timezone.utc)
    fin = (dia + timedelta(days=1)).astimezone(timezone.utc)
    rows = _leer_filas({"select": "payload,generado_en",
                        "generado_en": f"lt.{fin.isoformat()}",
                        "order": "generado_en.desc", "limit": 1})
    if not rows:
        return None
    payload = _payload_de(rows[0])
    if isinstance(payload, dict) and _es_anterior_a(rows[0].get("generado_en"), ini):
        payload["meta"].setdefault("warnings", []).append(
            f"Sin corrida del scraper el {fecha}: se muestra el análisis más cercano anterior.")
    return payload


def read_snapshot_history(limite: int = 120) -> list[dict]:
    """Serie histórica liviana para el gráfico de evolución: [{generado_en, candidatos}]
    ascendente (solo nombre/menciones/pct/... por candidato — el payload entero con
    evidencia sería pesadísimo). Se consulta desc con tope y se da vuelta en Python:
    con más snapshots que `limite` sobreviven los MÁS NUEVOS. [] si no hay / sin config."""
    rows = _leer_filas({"select": "generado_en,candidatos:payload->candidatos",
                        "order": "generado_en.desc", "limit": limite})
    if not rows:
        return []
    return [r for r in reversed(rows) if r.get("candidatos")]


def write_snapshot(payload: dict, generado_en: str) -> bool:
    """Guarda un snapshot nuevo (lo usa el scraper local). `generado_en` en ISO 8601.
    Devuelve True si se guardó, False si no."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR store.write: Supabase no configurado.")
        return False
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/{_TABLE}",
            json={"payload": payload, "generado_en": generado_en},
            headers={**_headers(), "Content-Type": "application/json"}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"ERROR store.write: {e}")
        return False
