# core_generator.py
import os
import random
import re
from dotenv import load_dotenv

from llm_fallback import llm
from logger_config import logger
from persona import get_icp_text, get_style_contract_text

from embeddings_manager import get_embedding, get_topics_collection, get_memory_collection
from style_guard import improve_style

# --- Pydantic Schemas for Structured Output ---
from typing import List
from pydantic import BaseModel, Field

class TweetDrafts(BaseModel):
    draft_a: str = Field(..., description="The first tweet draft, labeled as A.")
    draft_b: str = Field(..., description="The second tweet draft, labeled as B.")


load_dotenv()
openrouter_api_key = os.getenv("OPENROUTER_API_KEY")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# For production we use Gemini Pro for both generation and refinement.
# The actual Gemini model used is resolved by llm_fallback via GOOGLE_API_KEY,
# but we pass an indicative name for logs; chat_* ignores this for Gemini.
GENERATION_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
VALIDATION_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
# Less strict duplicate check by default; configurable via env
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.20") or 0.20)

# --- Post categories for third variant (English descriptions) ---
# Default categories (used if JSON file is missing or invalid)
DEFAULT_POST_CATEGORIES = [
    {
        "key": "contrast_statement",
        "name": "Declaración de Contraste",
        "pattern": (
            "Present two opposing ideas to create an instant reveal. Invalidate a common approach (Don't do X), then "
            "present a stronger alternative (Do Y) — position Y as the obvious solution."
        ),
    },
    {
        "key": "perspective_reframe",
        "name": "Reencuadre de Perspectiva",
        "pattern": (
            "Start with a universal truth the reader recognizes. Introduce a twist that reframes it — turn a negative "
            "(struggle) into a necessary element for a positive outcome (victory)."
        ),
    },
    {
        "key": "friction_reduction",
        "name": "Argumento de Reducción de Fricción",
        "pattern": (
            "Directly address analysis paralysis or fear to start. Break an intimidating goal into an absurdly small, "
            "manageable first step that motivates immediate action."
        ),
    },
    {
        "key": "identity_redefinition",
        "name": "Redefinición de Identidad",
        "pattern": (
            "Dismantle a limiting label (e.g., 'I'm not a salesperson'). Replace it with a simpler, authentic requirement "
            "that feels attainable and aligned with the reader's identity."
        ),
    },
    {
        "key": "parallel_contrast_aphorism",
        "name": "Aforismo de Contraste Paralelo",
        "pattern": (
            "Use parallel, aphoristic contrast to juxtapose two ideas. Start from a familiar saying (If A is B), then "
            "present a surprising counterpart (then C is D). Keep symmetry and punch."
        ),
    },
    {
        "key": "demonstrative_principle",
        "name": "Principio Demostrativo",
        "pattern": (
            "Teach a copywriting rule by showing. Contrast a 'bad' version (feature) with a 'good' version (benefit), "
            "then conclude with the principle demonstrated."
        ),
    },
    {
        "key": "counterintuitive_principle",
        "name": "Principio Contraintuitivo",
        "pattern": (
            "State a counterintuitive rule as a near-universal law that challenges a popular belief. Push the reader to "
            "adopt a more effective method by reframing the goal."
        ),
    },
    {
        "key": "process_promise",
        "name": "Promesa de Proceso",
        "pattern": (
            "Validate the reader's frustration: change isn't instant. Offer a future promise — tiny, consistent effort adds up "
            "to a total transformation. Encourage patience and trust in the process."
        ),
    },
    {
        "key": "common_villain_exposure",
        "name": "Exposición del Villano Común",
        "pattern": (
            "Recreate a recognizable negative scenario the reader detests. Expose and criticize the shared villain to build "
            "instant trust, positioning the writer as an ally."
        ),
    },
    {
        "key": "hidden_benefits_reveal",
        "name": "Revelación de Beneficios Ocultos",
        "pattern": (
            "Start with a promise to reveal the non-obvious value of an action. Use a short list to enumerate specific, "
            "unexpected benefits — make the abstract tangible."
        ),
    },
    {
        "key": "values_manifesto",
        "name": "Manifiesto de Valores",
        "pattern": (
            "Redefine a popular idea with a value hierarchy in a compact list. Use A > B comparisons to prioritize deep "
            "principles over superficial alternatives."
        ),
    },
    {
        "key": "delayed_gratification_formula",
        "name": "Fórmula de Gratificación Aplazada",
        "pattern": (
            "State a direct cause-effect between present sacrifice and future reward. Structure like: Do the hard thing today, "
            "get the desired outcome tomorrow. Motivate disciplined action."
        ),
    },
    {
        "key": "excuse_invalidation",
        "name": "Invalidación de Excusa",
        "pattern": (
            "Identify a common external excuse (the blamed villain). Then absolve it and redirect responsibility to an internal "
            "action or inaction, empowering the reader."
        ),
    },
    {
        "key": "revealing_definition",
        "name": "Definición Reveladora",
        "pattern": (
            "Redefine a known concept with a sharp metaphor that reveals its overlooked essence, raising its perceived value."
        ),
    },
    {
        "key": "fundamental_maxim",
        "name": "Máxima Fundamental",
        "pattern": (
            "Present a core principle as a non-negotiable rule of the domain. Reset priorities by exposing the true hierarchy."
        ),
    },
    {
        "key": "paradox_statement",
        "name": "Declaración Paradójica",
        "pattern": (
            "Drop a claim that sounds self-contradictory to break the reader's pattern. Hook curiosity, then resolve the paradox "
            "with a practical insight."
        ),
    },
    {
        "key": "shared_standard_appeal",
        "name": "Apelación al Estándar Compartido",
        "pattern": (
            "Establish a shared standard of excellence or value (If you do X...). Then present the call to action (...then we should do Y) "
            "as the logical consequence for those who belong to that group."
        ),
    },
]

# Optional external categories file (JSON)
POST_CATEGORIES_PATH = os.getenv(
    "POST_CATEGORIES_PATH",
    os.path.join(BASE_DIR, "config", "post_categories.json"),
)

_CACHED_POST_CATEGORIES = None

def load_post_categories():
    global _CACHED_POST_CATEGORIES
    if _CACHED_POST_CATEGORIES is not None:
        return _CACHED_POST_CATEGORIES
    try:
        if os.path.exists(POST_CATEGORIES_PATH):
            import json
            with open(POST_CATEGORIES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Validate shape (list of {key,name,pattern})
            valid = []
            for it in data if isinstance(data, list) else []:
                if not isinstance(it, dict):
                    continue
                k = (it.get("key") or "").strip()
                nm = (it.get("name") or "").strip()
                pt = (it.get("pattern") or "").strip()
                st = (it.get("structure") or "").strip()
                wy = (it.get("why") or "").strip()
                if k and nm and pt:
                    valid.append({"key": k, "name": nm, "pattern": pt, "structure": st, "why": wy})
            if valid:
                _CACHED_POST_CATEGORIES = valid
                logger.info(f"Loaded {len(valid)} post categories from JSON.")
                return _CACHED_POST_CATEGORIES
    except Exception as e:
        logger.warning(f"Failed to load post categories from '{POST_CATEGORIES_PATH}': {e}")
    _CACHED_POST_CATEGORIES = DEFAULT_POST_CATEGORIES
    return _CACHED_POST_CATEGORIES

def pick_random_post_category():
    categories = load_post_categories()
    return random.choice(categories)

# Categories where short bullet lists make sense for C
BULLET_CATEGORIES = {
    "hidden_benefits_reveal",
    "values_manifesto",
    "demonstrative_principle",
    "friction_reduction",
}

# Cargar contrato creativo e ICP desde el módulo persona
CONTRACT_TEXT = get_style_contract_text()
ICP_TEXT = get_icp_text()

# --- (Funciones auxiliares refine_... no cambian) ---
def refine_and_shorten_tweet(tweet_text: str, model: str) -> str:
    prompt = f'Your task is to shorten the following text to be under 280 characters. This is a hard limit. Preserve the core message and tone. Original Text: "{tweet_text}"'
    try:
        text = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": "You are a ruthless text editor."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return text
    except Exception: return tweet_text

# Eliminado recorte forzado: preferimos iterar con LLM hasta cumplir la longitud

def refine_single_tweet_style(raw_text: str, model: str) -> str:
    prompt = (
        "Rewrite the text to hit a sharper, NYC bar voice — smart, direct, a bit impatient — without losing the core insight.\n"
        "Do NOT add emojis or hashtags. Do NOT use Spanish.\n\n"
        "Amplifiers (must do):\n"
        "- Start with a punchy opener (no 'Most people…', no hedging).\n"
        "- Include one concrete image or tactical detail (micro‑visual).\n"
        "- Cut filler, adverbs, qualifiers (seems, maybe, might).\n"
        "- Strong verbs, short sentences, no corporate wording.\n"
        "- 2–4 short paragraphs separated by a blank line.\n"
        "- Keep under 280 characters.\n\n"
        f"RAW TEXT: --- {raw_text} ---"
    )
    system_message = (
        "You are a world-class ghostwriter rewriting text into a specific style. "
        "Follow the style contract exactly. Keep it concise and punchy.\n\n"
        "<STYLE_CONTRACT>\n" + CONTRACT_TEXT + "\n</STYLE_CONTRACT>\n\n"
        "Audience ICP:\n<ICP>\n" + ICP_TEXT + "\n</ICP>"
    )
    try:
        text = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
        )
        return text
    except Exception: return raw_text


def refine_single_tweet_style_flexible(raw_text: str, model: str) -> str:
    """Refine text to NYC bar voice without enforcing paragraph count.

    Allows a single punchy sentence, 1–3 short sentences, or up to 2 short paragraphs.
    Keeps <=280 char rule.
    """
    prompt = (
        "Rewrite the text to hit a sharper, NYC bar voice — smart, direct, a bit impatient — without losing the core insight.\n"
        "Do NOT add emojis or hashtags. Do NOT use Spanish.\n\n"
        "Amplifiers (must do):\n"
        "- Start with a punchy opener (no hedging).\n"
        "- Include one concrete image or tactical detail (micro‑visual).\n"
        "- Cut filler, adverbs, qualifiers (seems, maybe, might).\n"
        "- Strong verbs, short sentences, no corporate wording.\n\n"
        "Structure (flexible for C):\n"
        "- You MAY output a single hard‑hitting sentence.\n"
        "- Or 1–3 short sentences, same paragraph.\n"
        "- Or up to 2 very short paragraphs separated by one blank line.\n"
        "- If using bullets, use the '• ' bullet character (not hyphens), max 3 bullets, tight lines.\n"
        "- Keep under 280 characters.\n\n"
        f"RAW TEXT: --- {raw_text} ---"
    )
    system_message = (
        "You are a world-class ghostwriter rewriting text into a specific style. "
        "Follow the style contract exactly, EXCEPT paragraph-count rules are explicitly overridden for this variant.\n\n"
        "<STYLE_CONTRACT>\n" + CONTRACT_TEXT + "\n</STYLE_CONTRACT>\n\n"
        "Audience ICP:\n<ICP>\n" + ICP_TEXT + "\n</ICP>"
    )
    try:
        text = llm.chat_text(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
        )
        return text
    except Exception: return raw_text

def ensure_under_limit_via_llm(text: str, model: str, limit: int = 280, attempts: int = 4) -> str:
    """Itera con LLM hasta obtener un texto <= limit, sin recorte local."""
    attempt = 0
    best = text
    while attempt < attempts:
        attempt += 1
        prompt = (
            f"Rewrite the text so the TOTAL characters are <= {limit}. Preserve meaning and readability. "
            f"Do NOT add quotes, emojis or hashtags. Prefer short words and compact phrasing. Return ONLY JSON: "
            f"{{\"text\": \"<final text under {limit} chars>\"}}. Text must be <= {limit} characters.\n\n"
            f"TEXT: {best}"
        )
        try:
            data = llm.chat_json(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a ruthless editor returning strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.25,
            )
            candidate = (data or {}).get("text") if isinstance(data, dict) else None
            if isinstance(candidate, str) and candidate.strip():
                candidate = candidate.strip()
                if len(candidate) <= limit:
                    return candidate
                best = candidate
        except Exception:
            # Intento fallido, vuelve a intentar con el mejor candidato actual
            pass
    return best



def generate_third_tweet_variant(topic_abstract: str):
    """Generate a third tweet variant (EN - C) using a random post category pattern.

    Returns: (draft_c: str, category_name: str)
    """
    cat = pick_random_post_category()
    cat_name = cat["name"]
    cat_desc = cat["pattern"]
    cat_struct = (cat.get("structure") or "").strip()
    cat_why = (cat.get("why") or "").strip()

    try:
        use_bullets = cat.get("key") in BULLET_CATEGORIES

        prompt = f"""
**Audience:** Remember you are talking to a friend who fits the Ideal Customer Profile (ICP) below. Your tone should be like giving direct, valuable advice to them.

**Core Task:** Your goal is to follow the *spirit* and *rationale* of the category. The 'why' and 'pattern' are more important than a rigid adherence to the 'structure'. The output should make the reader feel a certain way or see *themselves* differently, as described in the category's rationale.

**Category Details:**
- Category: {cat_name}
- Pattern: {cat_desc}
- Structure: {('Structure template: ' + cat_struct) if cat_struct else ''}
- Rationale: {('Technique rationale: ' + cat_why) if cat_why else ''}

**Style and Output Rules:**
- **CRITICAL Constraint:** You MUST NOT use metaphors or analogies, even if they seem relevant. Express the idea in direct, literal language. The goal is a powerful, factual statement.
- Voice: NYC bar voice: smart, direct, slightly impatient; zero corporate tone.
- Structure: single hard-hitting sentence, 1–3 short sentences, or up to 2 very short paragraphs.
- Format: No emojis or hashtags. No quotes around the output. English only.
- Length: Keep under 280 characters (hard requirement).

**Topic:** {topic_abstract}
"""
        system_message = (
            "You are a world-class ghostwriter. Obey the following style contract strictly.\n\n<STYLE_CONTRACT>\n"
            + CONTRACT_TEXT + "\n</STYLE_CONTRACT>\n\n"
            "Audience ICP:\n<ICP>\n" + ICP_TEXT + "\n</ICP>"
        )

        raw_c = llm.chat_text(
            model=GENERATION_MODEL,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": (
                    prompt
                    + "\n\nOverride for C: Ignore any paragraph-count constraints from the style contract. "
                      "You may output a single strong sentence, 1–3 short sentences, or up to 2 very short paragraphs."
                )},
            ],
            temperature=0.75,
        )

        # Refine (flexible) and audit style
        c1 = refine_single_tweet_style_flexible(raw_c, VALIDATION_MODEL)
        try:
            improved_c, audit_c = improve_style(c1, CONTRACT_TEXT)
            c2 = improved_c or c1
        except Exception:
            c2 = c1

        # Ensure character limit
        if len(c2) > 280:
            c2 = ensure_under_limit_via_llm(c2, VALIDATION_MODEL, 280, attempts=4)

        return c2.strip(), cat_name
    except Exception as e:
        logger.error(f"Error generating third variant: {e}", exc_info=True)
        return "", cat_name

# --- FUNCIÓN DE GENERACIÓN MODIFICADA ---
def generate_tweet_from_topic(topic_abstract: str, ignore_similarity: bool = True):
    # --- Comprobación de Memoria ---
    if ignore_similarity:
        logger.info("Saltando comprobación de similitud por 'ignore_similarity=True'.")
    else:
        logger.info("Iniciando comprobación de similitud en memoria.")
        memory_collection = get_memory_collection()
        topic_embedding = get_embedding(topic_abstract)
        if topic_embedding and memory_collection.count() > 0:
            results = memory_collection.query(query_embeddings=[topic_embedding], n_results=1)
            try:
                distance = results and results['distances'][0][0]
                similar_id = results and results['ids'][0][0]
            except Exception:
                distance = None
                similar_id = None
            if isinstance(distance, (int, float)) and distance < SIMILARITY_THRESHOLD:
                logger.warning(f"Similitud detectada. Distancia: {distance:.4f} (Umbral: {SIMILARITY_THRESHOLD}). Tuit similar ID: {similar_id}.")
                # No bloquear la generación; solo loggear. La aprobación validará de nuevo.
        logger.info("Comprobación de similitud finalizada.")

    MAX_ATTEMPTS = 3
    for attempt in range(MAX_ATTEMPTS):
        logger.info(f"Intento de generación de IA {attempt + 1}/{MAX_ATTEMPTS}...")
        try:
            # --- PROMPT MODIFICADO PARA PEDIR DOS OPCIONES EN INGLÉS ---
            prompt = f"""
            You are a ghostwriter. Your task is to write TWO distinct alternatives for a tweet based on the topic below. Strictly follow the provided contract.

            **Contract for style and tone:**
            {CONTRACT_TEXT}
            ---
            **Topic:** {topic_abstract}

            **Style Amplifier (must do):**
            - NYC bar voice: smart, direct, slightly impatient; zero corporate tone.
            - Open with a punchy first line (no 'Most people…', no 'Counter‑intuitive truth:').
            - Include one concrete image or tactical detail (micro‑visual) to make it feel real.
            - No hedging or qualifiers (no seems/maybe/might). Strong verbs only.
            - 2–4 short paragraphs separated by a blank line. English only. No quotes around the output. No emojis/hashtags.
            - A and B MUST use different opening patterns (e.g., question vs. bold statement vs. vivid image).

            **CRITICAL OUTPUT REQUIREMENTS:**
            - Provide two high‑quality, distinct alternatives in English.
            - Both alternatives MUST be under 280 characters.
            - The output will be automatically structured, so do not add any labels like [EN - A] or [EN - B].
            """
            
            _gm = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
            logger.info(f"Llamando al modelo de generación (Gemini JSON): {_gm}.")

            draft_a = ""
            draft_b = ""
            try:
                # Solicitar JSON estricto con draft_a y draft_b (Gemini)
                resp = llm.chat_json(
                    model=GENERATION_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a world-class ghostwriter creating two tweet drafts. "
                                "Return ONLY strict JSON with exactly two string properties: draft_a and draft_b."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                prompt
                                + "\n\nOutput format (strict JSON): {\n  \"draft_a\": \"...\",\n  \"draft_b\": \"...\"\n}"
                            ),
                        },
                    ],
                    temperature=0.65,
                )
                if isinstance(resp, dict):
                    draft_a = str(resp.get("draft_a", "")).strip()
                    draft_b = str(resp.get("draft_b", "")).strip()
            except Exception as e_json:
                logger.warning(f"Intento {attempt + 1}: JSON generation failed, falling back to text parse: {e_json}")
                # Fallback: pedir dos variantes separadas por un delimitador claro
                plain = llm.chat_text(
                    model=GENERATION_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a world-class ghostwriter creating two tweet drafts."},
                        {"role": "user", "content": (
                            prompt
                            + "\n\nReturn two alternatives under 280 chars each."
                            + " Use the exact delimiter on a single line between them: ---"
                        )},
                    ],
                    temperature=0.65,
                )
                if isinstance(plain, str) and plain.strip():
                    if "\n---\n" in plain:
                        pA, pB = plain.split("\n---\n", 1)
                        draft_a = pA.strip()
                        draft_b = pB.strip()
                    else:
                        # Heurística: separar por doble salto de línea si no vino el delimitador
                        parts = [p.strip() for p in plain.split("\n\n") if p.strip()]
                        if len(parts) >= 2:
                            draft_a, draft_b = parts[0], parts[1]
                        else:
                            # última red: usa todo como A y reescribe B con leve variación
                            draft_a = plain.strip()
                            draft_b = plain.strip()[: max(0, len(plain.strip()) - 1)]

            if not draft_a or not draft_b:
                logger.warning(f"Intento {attempt + 1}: El modelo no generó una o ambas alternativas. Reintentando...")
                continue
            
            logger.info(f"Intento {attempt + 1}: Borradores A y B parseados. Iniciando refinamiento.")
            # Refinar estilo según contrato
            draft_a = refine_single_tweet_style(draft_a, VALIDATION_MODEL)
            draft_b = refine_single_tweet_style(draft_b, VALIDATION_MODEL)

            # Auditoría de estilo + posible reescritura creativa (sin listas cerradas)
            try:
                improved_a, audit_a = improve_style(draft_a, CONTRACT_TEXT)
                if improved_a and improved_a != draft_a:
                    logger.info(f"Auditoría A: se aplicó revisión de estilo. Detalle: {audit_a}")
                    draft_a = improved_a
                else:
                    logger.info(f"Auditoría A: sin cambios. Detalle: {audit_a}")
            except Exception as _:
                pass
            try:
                improved_b, audit_b = improve_style(draft_b, CONTRACT_TEXT)
                if improved_b and improved_b != draft_b:
                    logger.info(f"Auditoría B: se aplicó revisión de estilo. Detalle: {audit_b}")
                    draft_b = improved_b
                else:
                    logger.info(f"Auditoría B: sin cambios. Detalle: {audit_b}")
            except Exception as _:
                pass

            if len(draft_a) > 280:
                draft_a = ensure_under_limit_via_llm(draft_a, VALIDATION_MODEL, 280, attempts=4)
            if len(draft_b) > 280:
                draft_b = ensure_under_limit_via_llm(draft_b, VALIDATION_MODEL, 280, attempts=4)

            if len(draft_a) > 280 or len(draft_b) > 280:
                logger.warning(f"Intento {attempt + 1}: Alguna alternativa seguía >280 tras reescritura LLM. Reintentando...")
                continue

            logger.info(f"Intento {attempt + 1}: Borradores generados y validados con éxito.")
            return draft_a, draft_b

        except Exception as e:
            logger.error(f"Error crítico en el intento de generación {attempt + 1}: {e}", exc_info=True)

    logger.error("No se pudo generar un borrador válido tras varios intentos.")
    return "Error: No se pudo generar un borrador válido tras varios intentos.", ""

# --- (find_relevant_topic y find_topic_by_id no cambian en su lógica principal) ---
def find_relevant_topic(sample_size: int = 5):
    """Devuelve un tema. Si hay memoria, elige entre una muestra el menos similar a memoria."""
    logger.info("Buscando tema en 'topics_collection' (preferir menos similar a memoria)…")
    topics_collection = get_topics_collection()
    try:
        raw = topics_collection.get(include=[])  # type: ignore
        all_ids = raw.get('ids') if isinstance(raw, dict) else None
        if not all_ids:
            logger.warning("'topics_collection' está vacía. No se pueden encontrar temas.")
            return None
        # Aplanar si hiciera falta
        if isinstance(all_ids, list) and all_ids and isinstance(all_ids[0], list):
            ids = [x for sub in all_ids for x in sub]
        else:
            ids = all_ids

        # Muestra aleatoria
        candidates = random.sample(ids, min(sample_size, len(ids)))
        memory_collection = get_memory_collection()
        has_memory = False
        try:
            has_memory = memory_collection.count() > 0
        except Exception:
            has_memory = False

        best = None
        best_dist = -1.0

        for cid in candidates:
            td = topics_collection.get(ids=[cid], include=["documents", "metadatas"])  # type: ignore
            docs = td.get('documents') if isinstance(td, dict) else None
            if not docs:
                continue
            abstract = docs[0][0] if isinstance(docs[0], list) else docs[0]

            if has_memory:
                emb = get_embedding(abstract)
                if emb is not None:
                    try:
                        q = memory_collection.query(query_embeddings=[emb], n_results=1)
                        dist = q and q.get('distances') and q['distances'][0][0]
                        dist_val = float(dist) if isinstance(dist, (int, float)) else 0.0
                    except Exception:
                        dist_val = 0.0
                else:
                    dist_val = 0.0
            else:
                dist_val = 1.0  # sin memoria, cualquiera vale

            if dist_val > best_dist:
                best_dist = dist_val
                md = td.get('metadatas') or []
                pdf_name = None
                if md:
                    first_md = md[0][0] if isinstance(md[0], list) and md[0] else (md[0] if isinstance(md, list) else None)
                    if isinstance(first_md, dict):
                        pdf_name = first_md.get('pdf') or first_md.get('source_pdf')
                best = {"topic_id": cid, "abstract": abstract}
                if pdf_name:
                    best["source_pdf"] = pdf_name

        if best:
            logger.info(f"Tema seleccionado (menos similar en muestra {len(candidates)}), distancia≈{best_dist:.4f}")
            return best

        # Fallback total: random
        rid = random.choice(ids)
        td = topics_collection.get(ids=[rid], include=["documents"])  # type: ignore
        docs = td.get('documents') if isinstance(td, dict) else None
        if docs:
            abstract = docs[0][0] if isinstance(docs[0], list) else docs[0]
            return {"topic_id": rid, "abstract": abstract}
    except Exception as e:
        logger.error(f"Error al buscar un tema en ChromaDB: {e}", exc_info=True)
    return None

def find_topic_by_id(topic_id: str):
    # Aunque esta función no se usa, es buena práctica añadir logs si se decidiera usar en el futuro
    logger.info(f"Buscando tema específico por ID: {topic_id}")
    topics_collection = get_topics_collection()
    try:
        topic_data = topics_collection.get(ids=[topic_id], include=["documents", "metadatas"])  # type: ignore
        if topic_data and topic_data.get('documents'):
            logger.info(f"Tema con ID {topic_id} encontrado en ChromaDB.")
            docs = topic_data['documents']
            mds = topic_data.get('metadatas') or []
            topic_abstract = docs[0][0] if docs and isinstance(docs[0], list) else docs[0]
            pdf_name = None
            if mds:
                first_md = mds[0][0] if isinstance(mds[0], list) and mds[0] else (mds[0] if isinstance(mds, list) else None)
                if isinstance(first_md, dict):
                    pdf_name = first_md.get('pdf') or first_md.get('source_pdf')
            result = {"topic_id": topic_id, "abstract": topic_abstract}
            if pdf_name:
                result["source_pdf"] = pdf_name
            return result
    except Exception as e:
        logger.error(f"Error al buscar tema por ID {topic_id} en ChromaDB: {e}", exc_info=True)
    
    logger.warning(f"Tema con ID {topic_id} no encontrado en ChromaDB. Fallback no implementado.")
    # Fallback a JSON...
    return None
