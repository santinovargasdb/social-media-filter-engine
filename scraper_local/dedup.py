"""
Dedup de posts entre capturas solapadas (Fase 3 del scraper local).

El mismo post aparece en capturas consecutivas (el scroll se solapa ~10%) y en
búsquedas de candidatos distintos. Dos criterios:

1. Duplicado EXACTO: misma (red, autor, hash del texto normalizado) → se descarta.
2. Casi-duplicado de UNA cuenta: la misma persona repostea el mismo contenido con
   otra reacción pegada adelante (ej. una risa "jajaja") → los textos difieren al
   principio pero comparten casi todas las palabras. Dentro de una misma cuenta,
   si dos textos tienen una similitud de palabras (Jaccard) >= _SIM_UMBRAL, se
   tratan como el mismo post. Cuentas DISTINTAS nunca se colapsan: dos personas
   citando lo mismo son publicaciones legítimas.

Puro (sin I/O): se testea completo en CI, incluido un caso real del snapshot.
"""
import hashlib
import unicodedata

# El texto visible de un post puede variar en la cola (links/menciones cortadas
# por el borde): comparar los primeros 200 chars alcanza para el hash exacto.
_TEXTO_CHARS = 200

# Similitud de palabras (Jaccard, sin tildes) para el casi-duplicado de una cuenta.
# Medido sobre datos reales (snapshot 2026-10-05): el mismo clip de Kicillof
# reposteado con otra risa adelante dio 0.875; dos tuits DISTINTOS de la misma
# cuenta, 0.19. 0.7 separa ambos con margen. El piso de palabras evita comparar
# textos muy cortos (truncados/"...") que son ruido y darían falsos positivos.
_SIM_UMBRAL = 0.7
_SIM_MIN_TOKENS = 5


def _plegar(texto: str) -> str:
    """Minúsculas y sin marcas diacríticas (ñ→n, tildes fuera) vía NFD."""
    d = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in d if not unicodedata.combining(c))


def _norma(texto: str) -> str:
    """Texto en minúsculas con espacios colapsados (para el hash exacto)."""
    return " ".join((texto or "").lower().split())


def _tokens(texto: str) -> set:
    """Conjunto de palabras alfanuméricas, sin tildes (para comparar contenido)."""
    base = _plegar(texto)
    return {w for w in "".join(c if c.isalnum() else " " for c in base).split() if w}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cuenta(post: dict) -> tuple:
    red = (post.get("red") or "").strip().lower()
    autor = (post.get("autor") or "").strip().lower().lstrip("@")
    return (red, autor)


def _clave(post: dict, indice: int) -> tuple:
    red, autor = _cuenta(post)
    texto = _norma(post.get("texto"))[:_TEXTO_CHARS]
    if not autor and not texto:
        # Sin autor ni texto no hay identidad: que no colapsen entre sí.
        return ("", "", indice)
    return (red, autor, hashlib.md5(texto.encode("utf-8")).hexdigest()[:16])


def _repite_a_la_cuenta(tokens: set, previos: list[set]) -> bool:
    """True si `tokens` se parece (Jaccard >= umbral) a algún post ya conservado
    de la misma cuenta, con ambos por encima del piso de palabras."""
    if len(tokens) < _SIM_MIN_TOKENS:
        return False
    return any(len(p) >= _SIM_MIN_TOKENS and _jaccard(tokens, p) >= _SIM_UMBRAL
               for p in previos)


def dedup_posts(posts: list[dict]) -> list[dict]:
    """Conserva la PRIMERA aparición de cada post (orden estable).

    Descarta duplicados exactos (misma red, autor y texto) y, dentro de una misma
    cuenta, casi-duplicados por similitud de palabras (el mismo contenido con otra
    reacción adelante). Cuentas distintas nunca se colapsan.
    """
    vistos: set = set()
    tokens_por_cuenta: dict[tuple, list[set]] = {}
    out: list[dict] = []
    for i, post in enumerate(posts):
        k = _clave(post, i)
        if k in vistos:
            continue
        cuenta = _cuenta(post)
        tokens = _tokens(post.get("texto"))
        if cuenta[1] and _repite_a_la_cuenta(tokens, tokens_por_cuenta.get(cuenta, [])):
            continue
        vistos.add(k)
        out.append(post)
        tokens_por_cuenta.setdefault(cuenta, []).append(tokens)
    return out
