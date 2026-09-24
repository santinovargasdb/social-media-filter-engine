"""
Dedup de posts entre capturas solapadas (Fase 3 del scraper local).

El mismo post aparece en capturas consecutivas (el scroll se solapa ~10%) y en
búsquedas de candidatos distintos. Clave: (red, autor normalizado, hash del texto
normalizado). Puro (sin I/O): se testea completo en CI.
"""
import hashlib

# El texto visible de un post puede variar en la cola (links/menciones cortadas
# por el borde): comparar los primeros 200 chars alcanza para identificarlo.
_TEXTO_CHARS = 200


def _clave(post: dict, indice: int) -> tuple:
    red = (post.get("red") or "").strip().lower()
    autor = (post.get("autor") or "").strip().lower().lstrip("@")
    texto = " ".join((post.get("texto") or "").lower().split())[:_TEXTO_CHARS]
    if not autor and not texto:
        # Sin autor ni texto no hay identidad: que no colapsen entre sí.
        return ("", "", indice)
    return (red, autor, hashlib.md5(texto.encode("utf-8")).hexdigest()[:16])


def dedup_posts(posts: list[dict]) -> list[dict]:
    """Conserva la PRIMERA aparición de cada post (orden estable)."""
    vistos: set = set()
    out: list[dict] = []
    for i, post in enumerate(posts):
        k = _clave(post, i)
        if k in vistos:
            continue
        vistos.add(k)
        out.append(post)
    return out
