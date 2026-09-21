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

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
_TABLE = os.environ.get("URNA_SNAPSHOT_TABLE", "urna_snapshot")
_TIMEOUT = 15


def _headers() -> dict:
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def read_latest_snapshot() -> dict | None:
    """Devuelve el último snapshot guardado (dict con la forma del resultado de la urna,
    con `meta.ultima_actualizacion` inyectada), o None si no hay / no está configurado /
    falló la lectura."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("DEBUG store: Supabase no configurado (SUPABASE_URL/SUPABASE_KEY).")
        return None
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/{_TABLE}",
            params={"select": "payload,generado_en", "order": "generado_en.desc", "limit": 1},
            headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
    except requests.exceptions.RequestException as e:
        print(f"ERROR store.read: {e}")
        return None
    if not rows:
        return None
    payload = rows[0].get("payload") or None
    if isinstance(payload, dict):
        payload.setdefault("meta", {})["ultima_actualizacion"] = rows[0].get("generado_en")
    return payload


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
