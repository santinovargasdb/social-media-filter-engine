"""
Fase 2 del scraper local — lector de capturas por visión.

Manda UNA captura de pantalla a Gemini (visión, transporte compartido del backend:
misma cascada de modelos y rotación de keys) con un prompt que extrae Y clasifica
los posts visibles en una sola pasada, con las MISMAS reglas direccionales que el
clasificador de texto (importa electoral.REGLAS_CANDIDATOS). Devuelve posts
saneados listos para agregar (Fase 3: dedup + aggregate + snapshot).

Uso como CLI (tuning en la PC de la oficina):
    python vision.py captura.png --red twitter [--candidatos "A,B"]

Paceo free-tier: VISION_MIN_INTERVAL segundos entre llamadas (default 10; 0 = sin
espera, para tests o API paga).
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

# El scraper corre desde el repo clonado en la PC de la oficina: reusa el código
# del backend agregándolo al path (mismo patrón que usará run.py en Fase 3).
BACKEND_DIR = str(Path(__file__).resolve().parent.parent / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import gemini_client  # noqa: E402
from electoral import CANDIDATOS_DEFAULT, POSTURAS_VALIDAS, REGLAS_CANDIDATOS, canonical_key  # noqa: E402

_MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# Reloj monótono de la última llamada a Gemini (el batch es secuencial).
_last_call = 0.0


def _load_image(image, mime: str | None = None) -> tuple[str, str]:
    """`image` es una ruta (str/Path) o bytes. Devuelve (base64, mime_type)."""
    if isinstance(image, (str, Path)):
        path = Path(image)
        data = path.read_bytes()
        mime = mime or _MIMES.get(path.suffix.lower(), "image/png")
    else:
        data = bytes(image)
        mime = mime or "image/png"
    return base64.b64encode(data).decode("ascii"), mime


def _build_vision_prompt(red: str, candidatos: list[str]) -> str:
    lista = ", ".join(candidatos)
    return f"""Sos un analista de opinión pública que evalúas publicaciones de redes sociales del ámbito argentino de cara a las próximas elecciones presidenciales.

Vas a recibir UNA CAPTURA DE PANTALLA de la red social "{red}". Tu tarea es EXTRAER cada publicación visible y clasificarla, en una sola pasada.

REGLAS DE LECTURA DE PANTALLA (OBLIGATORIAS):
- Extraé SOLO las publicaciones COMPLETAMENTE visibles. Si una publicación está cortada por un borde de la captura, ignorala.
- Ignorá la interfaz de la red: menús, buscadores, tendencias, sugerencias ("a quién seguir"), contadores de interacción y publicidad (todo lo marcado "Promocionado" o "Ad"). Las publicidades NO van en la lista de salida — no las incluyas ni siquiera con "es_electoral": false.
- Las publicaciones comunes que NO son electorales (deporte, espectáculos, etc.) SÍ van en la lista, con "es_electoral": false. Lo ÚNICO que se excluye de la lista es la publicidad, la interfaz y las publicaciones cortadas.
- NO inventes NADA y NO "corrijas" lo que leas: transcribí textos y nombres EXACTO como aparecen en pantalla, aunque parezcan tener errores. "autor": el @usuario (el handle que empieza con @) si está visible; SOLO si no se ve ningún @, usá el nombre mostrado. "fecha": el texto de fecha TAL CUAL aparece en pantalla (ej. "2 h", "12 sep."). Si un dato no se ve, dejá "".
- "texto": el texto completo de la publicación tal como se lee en la captura.
- REGLA DE AISLAMIENTO: evaluá cada publicación de forma totalmente AISLADA e INDEPENDIENTE de las demás.

Por cada publicación determiná:
1. "es_electoral": true solo si la publicación habla de candidatos, partidos o la contienda electoral presidencial argentina; false si es ruido, spam u otro tema.
2. "candidatos": lista de los candidatos presidenciales mencionados. Por cada uno:
{REGLAS_CANDIDATOS}
3. "cita": el fragmento textual breve de la publicación que justifica la señal (o "" si no aplica).

ACLARACIÓN para la REGLA DE ENCUESTAS/SONDEOS: "competitivo" incluye a cualquier candidato que está a pocos puntos del puntero (pelea la punta) → también va "a_favor". "Intermedio" (→ "neutro") es el que está claramente lejos de la punta pero no marginal.

Candidatos de referencia (lista NO exhaustiva; puede aparecer alguno que no esté acá): {lista}.

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código): una LISTA con un objeto por publicación visible, en el orden en que aparecen. Formato exacto:
[
  {{ "autor": "@usuario", "fecha": "2 h", "texto": "...", "es_electoral": true, "candidatos": [{{ "nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9 }}], "cita": "..." }}
]
Si no hay publicaciones legibles, devolvé []."""


def _sanitize_posts(parsed: list, red: str) -> list[dict]:
    """Sanea la respuesta de Gemini (mismo espíritu que electoral._analyze_electoral_batch):
    posts sin texto afuera, posturas fuera del enum afuera, confianza clampeada a [0,1]."""
    out: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        texto = (item.get("texto") or "").strip()
        if not texto:
            continue
        candidatos_saneados = []
        for c in item.get("candidatos", []) or []:
            if not isinstance(c, dict):
                continue
            nombre = (c.get("nombre") or "").strip()
            postura = (c.get("postura") or "").strip().lower()
            if not nombre or not canonical_key(nombre) or postura not in POSTURAS_VALIDAS:
                continue
            try:
                conf = float(c.get("confianza", 0))
            except (TypeError, ValueError):
                conf = 0.0
            conf = min(max(conf, 0.0), 1.0)
            candidatos_saneados.append({"nombre": nombre, "postura": postura, "confianza": conf})
        out.append({
            "texto": texto,
            "autor": (item.get("autor") or "").strip(),
            "fecha": (item.get("fecha") or "").strip(),
            "red": red,
            "es_electoral": bool(item.get("es_electoral", False)),
            "candidatos": candidatos_saneados,
            "cita": (item.get("cita") or "").strip(),
        })
    return out


def read_capture(image, red: str, candidatos: list[str] | None = None,
                 mime: str | None = None) -> list[dict] | None:
    """Lee UNA captura con Gemini visión. Devuelve los posts saneados, [] si no se
    vio ninguno, o None si el transporte falló (mismo contrato que electoral)."""
    data_b64, mime = _load_image(image, mime)
    prompt = _build_vision_prompt(red, candidatos or CANDIDATOS_DEFAULT)
    _pace()
    parsed, _status = gemini_client.run_with_rotation(prompt, image=(data_b64, mime))
    if parsed is None:
        print("ERROR vision: Gemini no devolvió resultado (upstream).")
        return None
    return _sanitize_posts(parsed, red)


def _pace() -> None:
    """Espera lo que falte del intervalo mínimo entre llamadas (free-tier)."""
    global _last_call
    try:
        interval = float(os.environ.get("VISION_MIN_INTERVAL", "10"))
    except ValueError:
        interval = 10.0
    now = time.monotonic()
    if interval > 0 and _last_call > 0:
        restante = interval - (now - _last_call)
        if restante > 0:
            time.sleep(restante)
    _last_call = time.monotonic()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Lee una captura de una red social con Gemini visión y devuelve los posts en JSON.")
    ap.add_argument("captura", help="ruta de la imagen (png/jpg/webp)")
    ap.add_argument("--red", default="twitter", help="red de la captura (default: twitter)")
    ap.add_argument("--candidatos", default="",
                    help="lista separada por comas (default: CANDIDATOS_DEFAULT del backend)")
    args = ap.parse_args(argv)
    candidatos = [c.strip() for c in args.candidatos.split(",") if c.strip()] or None
    posts = read_capture(args.captura, args.red, candidatos)
    if posts is None:
        print("ERROR: Gemini no respondió (¿GEMINI_API_KEY configurada? ¿cuota?).", file=sys.stderr)
        return 1
    print(json.dumps(posts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
