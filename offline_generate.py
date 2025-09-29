import os
import random
import re
import sys

# Permite ejecutar desde cualquier ruta
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from embeddings_manager import get_topics_collection  # type: ignore


def pick_random_topic_text() -> str:
    coll = get_topics_collection()
    total = coll.count()  # type: ignore
    if not total:
        raise SystemExit("No hay temas en 'topics_collection'. Ingresa un PDF primero.")
    offset = random.randrange(0, max(total - 1, 1))
    res = coll.get(include=["documents"], limit=1, offset=offset)  # type: ignore
    docs = res.get("documents") or []
    text = docs[0] if docs else ""
    if not text:
        raise SystemExit("No se pudo leer un documento de la colección.")
    return text.strip()


def split_sentences(text: str):
    # División simple por puntuación común
    parts = re.split(r"(?<=[\.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def trim_to_limit(s: str, limit: int = 280) -> str:
    if len(s) <= limit:
        return s
    # Cortar por palabra
    cut = s[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


def generate_variations(text: str):
    sents = split_sentences(text)
    core = " ".join(sents[:2]) if sents else text
    core = re.sub(r"\s+", " ", core).strip()

    # Variación A: Hook + insight breve
    hook = "Idea clave:"
    vA = f"{hook} {core} \n\nSi lideras, simplifica, delega y mide impacto.".strip()
    vA = trim_to_limit(vA)

    # Variación B: 3 bullets accionables
    bullets = []
    if sents:
        bullets = [sents[0]]
        if len(sents) > 1:
            bullets.append(sents[1])
    fillers = [
        "Foco en lo esencial. No escales complejidad.",
        "Delegar no es rendirse: es escalar.",
        "Mide resultados, no actividad.",
    ]
    i = 0
    while len(bullets) < 3:
        bullets.append(fillers[i % len(fillers)])
        i += 1
    bullets = bullets[:3]
    b_lines = ["• " + re.sub(r"\s+", " ", b).strip() for b in bullets]
    vB = "Hazlo así:\n" + "\n".join(b_lines)
    vB = trim_to_limit(vB)

    return vA, vB


def main():
    text = pick_random_topic_text()
    a, b = generate_variations(text)
    print("TEMA:", trim_to_limit(text, 200))
    print("\n--- Opción A ---\n" + a)
    print("\n--- Opción B ---\n" + b)


if __name__ == "__main__":
    main()
