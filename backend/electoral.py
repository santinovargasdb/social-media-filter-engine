"""
Capa 3 — Boca de Urna (Termómetro de Redes).

Reutiliza normalizer.fetch_posts (que a su vez usa fetcher/SerpAPI) para traer
publicaciones públicas, corre un análisis electoral con Gemini (candidato +
postura + confianza por post vía gemini_client), agrega en sentimiento neto por
candidato, y compara la brecha contra las consultoras cargadas por CSV.

No habla con SerpAPI ni con Gemini directamente: fetch vía normalizer, transporte
vía gemini_client. Stateless.
"""
import csv
import io
import json
import unicodedata

import gemini_client

CONF_MIN = 0.5
POSTURAS_VALIDAS = ("a_favor", "en_contra", "neutro")
CSV_COLUMNS = ("consultora", "fecha", "candidato", "porcentaje")


def _parse_pct(raw: str) -> float:
    """Convierte '42,5' o '42.5' a float. Lanza ValueError si no es numérico."""
    return float(str(raw).strip().replace(",", "."))


def _valid_iso_date(raw: str) -> bool:
    import datetime
    try:
        datetime.date.fromisoformat(str(raw).strip())
        return True
    except ValueError:
        return False


def parse_pollster_csv(text: str) -> tuple[list[dict], list[str]]:
    """CSV de consultoras -> (filas_validas, warnings). Header inválido -> ValueError."""
    text = (text or "").strip()
    if not text:
        return [], []
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    faltantes = [c for c in CSV_COLUMNS if c not in header]
    if faltantes:
        raise ValueError(f"CSV inválido: faltan columnas {faltantes}. Se esperan {list(CSV_COLUMNS)}.")

    rows: list[dict] = []
    warnings: list[str] = []
    for i, raw in enumerate(reader, start=2):  # fila 1 = header
        consultora = (raw.get("consultora") or "").strip()
        fecha = (raw.get("fecha") or "").strip()
        candidato = (raw.get("candidato") or "").strip()
        if not consultora or not candidato:
            warnings.append(f"Fila {i}: consultora o candidato vacío — salteada.")
            continue
        if not _valid_iso_date(fecha):
            warnings.append(f"Fila {i}: fecha '{fecha}' no es YYYY-MM-DD — salteada.")
            continue
        try:
            pct = _parse_pct(raw.get("porcentaje", ""))
        except (TypeError, ValueError):
            warnings.append(f"Fila {i}: porcentaje '{raw.get('porcentaje')}' no numérico — salteada.")
            continue
        rows.append({"consultora": consultora, "fecha": fecha,
                     "candidato": candidato, "porcentaje": pct})
    return rows, warnings


def _build_electoral_prompt(items_para_prompt: list[dict]) -> str:
    items_text = json.dumps(items_para_prompt, ensure_ascii=False, indent=2)
    return f"""Sos un analista de opinión pública que evalúa publicaciones de redes sociales del ámbito argentino de cara a las próximas elecciones presidenciales.

Vas a recibir una lista de publicaciones en JSON. Cada una tiene un "id" único ("Post_0", "Post_1", ...), su "texto" y la "red".

REGLA DE AISLAMIENTO (OBLIGATORIA): Evaluá cada publicación de forma totalmente AISLADA e INDEPENDIENTE. Está PROHIBIDO que una publicación influya en el análisis de otra. Tratá cada "id" como un caso separado.

Por cada publicación determiná:
1. "es_electoral": true solo si la publicación habla de candidatos, partidos o la contienda electoral presidencial argentina; false si es ruido, spam u otro tema.
2. "candidatos": lista de los candidatos presidenciales mencionados. Por cada uno:
   - "nombre": el nombre COMPLETO y CANÓNICO del candidato (ej. si dice "Milei" o "el León", devolvé "Javier Milei"). Unificá alias y apodos al nombre canónico.
   - "postura": la postura del AUTOR del posteo hacia ese candidato. Exactamente uno de: "a_favor", "en_contra", "neutro".
   - "confianza": número entre 0 y 1 con tu certeza sobre esa postura.
   Si no hay candidatos, devolvé [].
3. "cita": el fragmento textual breve del posteo que justifica la postura (o "" si no aplica).

Devolvé ÚNICAMENTE un JSON válido (sin texto adicional ni bloques de código) que sea un ESPEJO EXACTO de los IDs recibidos: un objeto por publicación, con su mismo "id". No agregues ni omitas ninguno. Formato exacto:
[
  {{ "id": "Post_0", "es_electoral": true, "candidatos": [{{ "nombre": "Javier Milei", "postura": "a_favor", "confianza": 0.9 }}], "cita": "..." }}
]

Publicaciones a evaluar:
{items_text}"""


def analyze_posts_electoral(posts_by_id: dict[str, dict]) -> list[dict] | None:
    """Clasifica cada post (candidato + postura + confianza) vía Gemini. Devuelve
    el mirror saneado por id, o None si el transporte falló (upstream)."""
    if not posts_by_id:
        return []
    items_para_prompt = [
        {"id": pid, "red": src.get("network", ""), "texto": src.get("text", "") or ""}
        for pid, src in posts_by_id.items()
    ]
    prompt = _build_electoral_prompt(items_para_prompt)
    parsed, _status = gemini_client.run_with_rotation(prompt)
    if parsed is None:
        return None

    out: list[dict] = []
    for item in parsed:
        pid = item.get("id", "") or ""
        if pid not in posts_by_id:
            continue  # id inventado o repetido por la IA
        candidatos_saneados = []
        for c in item.get("candidatos", []) or []:
            nombre = (c.get("nombre") or "").strip()
            postura = (c.get("postura") or "").strip().lower()
            if not nombre or postura not in POSTURAS_VALIDAS:
                continue
            try:
                conf = float(c.get("confianza", 0))
            except (TypeError, ValueError):
                conf = 0.0
            candidatos_saneados.append({"nombre": nombre, "postura": postura, "confianza": conf})
        out.append({
            "id": pid,
            "candidatos": candidatos_saneados,
            "cita": (item.get("cita") or "").strip(),
            "es_electoral": bool(item.get("es_electoral", False)),
        })
    return out
