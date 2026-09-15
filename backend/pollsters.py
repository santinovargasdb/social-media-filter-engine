"""
Capa 3 — Consultoras automáticas (Boca de Urna).

Convierte una lista fija de consultoras en filas de comparación
{consultora, fecha, candidato, porcentaje, fuente_url, fuente_titulo}, extraídas
de notas periodísticas con Gemini y atadas SIEMPRE al link de la nota (fidelidad:
espejo fiel + fuente verificable).

No habla con SerpAPI ni Gemini "a mano": fetch vía fetcher.search_serpapi_web,
extracción vía gemini_client. `electoral` importa este módulo de forma diferida
para evitar el ciclo (este módulo importa canonical_key de electoral).
"""
from electoral import canonical_key


# Lista fija EDITABLE de consultoras a seguir. Elegidas por prestigio metodológico
# y equilibrio de espectro (3 neutrales + 1 de cada polo). La "afiliación" es
# percepción pública debatida, ninguna se declara partidaria.
CONSULTORAS_DEFAULT = [
    "Opinaia",           # neutral / profesional
    "Poliarquía",        # neutral / establishment
    "Management & Fit",  # neutral / centro
    "CB Consultora",     # suele medir mejor al oficialismo/LLA
    "Zuban Córdoba",     # suele medir mejor al peronismo/kirchnerismo
]

# Descriptor de la elección para la query (editable, para no hardcodear el año).
ELECCION_LABEL = "presidencial 2027"


def _parse_pct(raw) -> float:
    """'42,5' | '42.5' | 42 -> float. Lanza ValueError/TypeError si no es numérico."""
    return float(str(raw).strip().replace(",", "."))


def _parse_extraction(parsed: list, consultora: str, articles: list[dict]) -> list[dict]:
    """Mapea la respuesta de Gemini (espejo por 'Art_i') a filas de consultora con
    su fuente. Descarta ids inventados y porcentajes no numéricos. La fecha sale de
    la fila; si no vino, cae a la 'date' del artículo."""
    by_id = {f"Art_{i}": a for i, a in enumerate(articles)}
    rows: list[dict] = []
    for item in parsed or []:
        art = by_id.get(item.get("id", ""))
        if art is None:
            continue
        fecha = (item.get("fecha") or "").strip() or (art.get("date") or "").strip()
        for fila in item.get("filas", []) or []:
            candidato = (fila.get("candidato") or "").strip()
            if not candidato:
                continue
            try:
                pct = _parse_pct(fila.get("porcentaje"))
            except (TypeError, ValueError):
                continue
            rows.append({
                "consultora": consultora,
                "fecha": fecha,
                "candidato": candidato,
                "porcentaje": pct,
                "fuente_url": art.get("url", ""),
                "fuente_titulo": art.get("title", ""),
            })
    return rows


def _dedup_latest(rows: list[dict]) -> list[dict]:
    """Por (consultora, candidato canónico) conserva la fila de fecha más reciente."""
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r["consultora"].strip().lower(), canonical_key(r["candidato"]))
        cur = best.get(key)
        if cur is None or r.get("fecha", "") > cur.get("fecha", ""):
            best[key] = r
    return list(best.values())
