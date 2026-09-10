"""
Transporte compartido de Gemini: llamada HTTP cruda, parseo JSON, cascada de
modelos con fallback por status y rotación a la API Key secundaria por cuota.

Extraído de normalizer.py para que la Capa 3 de scoring (normalizer) y la Capa 3
electoral (electoral.py) usen el mismo transporte sin duplicarlo. No conoce el
dominio: recibe un prompt, devuelve la lista JSON que Gemini responde.
"""
import os
import json

import requests

# Cascada de modelos: si el primero da 429/503/404 se prueba el siguiente.
GEMINI_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
)
GEMINI_RETRY_STATUSES = (429, 503, 404)


def generate_raw(model: str, api_key: str, prompt: str, timeout: int = 45) -> tuple[str | None, int | None]:
    """Llamada cruda a un modelo Gemini. Devuelve (texto_sin_fences, http_status_si_error).
    - (texto, None): éxito       - (None, status): error HTTP       - (None, None): error de red."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        response.raise_for_status()
        res_data = response.json()
        raw = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return raw, None
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        body_msg = ""
        if e.response is not None:
            try:
                body = e.response.json()
                body_msg = body.get("error", {}).get("message", "") or str(body)[:200]
            except Exception:
                body_msg = (e.response.text or "")[:200]
        print(f"ERROR Gemini[{model}]: HTTP {status} — {body_msg}")
        return None, status
    except Exception as e:
        print(f"ERROR Gemini[{model}]: {e}")
        return None, None


def call_gemini_json(model: str, api_key: str, prompt: str) -> tuple[list | None, int | None]:
    """Llama a un modelo y parsea la respuesta como LISTA JSON (mirror por id).
    - (lista, None): éxito     - (None, status): error HTTP     - (None, None): red/parseo."""
    raw, status = generate_raw(model, api_key, prompt, timeout=45)
    if raw is None:
        return None, status
    try:
        parsed = json.loads(raw)
    except Exception as e:
        print(f"ERROR Gemini[{model}]: parseo JSON falló — {e}")
        return None, None
    if not isinstance(parsed, list):
        print(f"ERROR Gemini[{model}]: respuesta no es lista JSON")
        return None, None
    return parsed, None


def run_cascade(prompt: str, api_key: str) -> tuple[list | None, int | None]:
    """Recorre GEMINI_MODELS con UNA api_key; el primero que responde OK gana; el
    siguiente solo se prueba ante 429/503/404. Devuelve (parsed, last_status)."""
    parsed: list | None = None
    status: int | None = None
    for idx, model in enumerate(GEMINI_MODELS):
        parsed, status = call_gemini_json(model, api_key, prompt)
        if parsed is not None:
            if idx > 0:
                print(f"DEBUG Gemini: fallback EXITOSO con {model}")
            return parsed, status
        if status not in GEMINI_RETRY_STATUSES:
            return None, status
        if idx + 1 < len(GEMINI_MODELS):
            print(f"DEBUG Gemini: HTTP {status} en {model}, probando fallback {GEMINI_MODELS[idx + 1]}")
    return None, status


def run_with_rotation(prompt: str) -> tuple[list | None, int | None]:
    """Cascada con la API Key PRINCIPAL; si la cuota se agotó (429/503) y no quedó
    resultado, rota a la SECUNDARIA y reintenta el mismo prompt. Devuelve
    (parsed, last_status). Sin GEMINI_API_KEY devuelve (None, None)."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY no configurada.")
        return None, None
    parsed, status = run_cascade(prompt, api_key)
    if parsed is None and status in (429, 503):
        secondary_key = os.getenv("GEMINI_API_KEY_SECONDARY", "")
        if secondary_key:
            print("Cuota principal agotada. Rotando a la API de SMATA...")
            parsed, status = run_cascade(prompt, secondary_key)
            if parsed is not None:
                print("DEBUG Gemini: rotación a API secundaria EXITOSA.")
        else:
            print("DEBUG Gemini: cuota principal agotada y GEMINI_API_KEY_SECONDARY no configurada — sin rotación.")
    return parsed, status
