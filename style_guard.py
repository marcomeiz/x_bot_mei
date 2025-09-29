import os
from typing import Dict, Any, Tuple

from dotenv import load_dotenv

from logger_config import logger
from llm_fallback import llm
from embeddings_manager import get_embedding, get_memory_collection


load_dotenv()

# Cargar contrato (compartido para generador y watchers)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CONTRACT_FILE = os.path.join(BASE_DIR, "copywriter_contract.md")
CONTRACT_TEXT = ""
try:
    with open(CONTRACT_FILE, "r", encoding="utf-8") as _f:
        CONTRACT_TEXT = _f.read().strip()
except Exception as _e:
    CONTRACT_TEXT = (
        "Style: airy, personal, witty; 2-4 short paragraphs; personal voice; "
        "subtle wit; show, don't announce; English only; <=280 chars; two variants A and B."
    )

ENFORCE_STYLE_AUDIT = os.getenv("ENFORCE_STYLE_AUDIT", "1").lower() in ("1", "true", "yes", "y")
STYLE_REVISION_ROUNDS = int(os.getenv("STYLE_REVISION_ROUNDS", "1"))


_JARGON_LIST = {
    # Corporate/business cliches that flatten the tone
    "synergy", "paradigm", "stakeholder", "deliverables", "leverage",
    "empower", "optimize", "optimization", "enablement", "alignment",
    "ecosystem", "best practices", "bandwidth", "low-hanging fruit",
    "wheelhouse", "move the needle", "game changer", "thought leadership",
}


def _detect_corporate_jargon(text: str) -> int:
    t = text.lower()
    hits = 0
    for w in _JARGON_LIST:
        if w in t:
            hits += 1
    return hits


def _style_similarity_to_memory(text: str) -> float:
    """Returns a similarity score to previously approved tweets (0..1). If no memory, returns 0.0.
    Uses cosine distance from Chroma: score = 1 - avg(distance of top-3).
    """
    try:
        coll = get_memory_collection()
        if coll.count() == 0:
            return 0.0
        emb = get_embedding(text)
        if not emb:
            return 0.0
        res = coll.query(query_embeddings=[emb], n_results=3)
        dists = res.get("distances") or []
        if not dists or not dists[0]:
            return 0.0
        avg_d = sum(dists[0]) / len(dists[0])
        score = max(0.0, min(1.0, 1.0 - avg_d))
        return score
    except Exception as e:
        logger.warning(f"Style similarity check failed: {e}")
        return 0.0


def audit_style(text: str, contract_text: str) -> Dict[str, Any]:
    """LLM-based style audit returning a rubric. Conservative JSON parsing via llm.chat_json."""
    prompt = f"""
Evaluate the following text against the style contract. Do not rewrite.
Return ONLY strict JSON with fields:
{{
  "english_only": boolean,
  "paras": integer,               // number of paragraphs (separated by blank lines)
  "voice": "bar"|"boardroom",    // bar = conversational, personal, witty; boardroom = corporate, generic
  "local_flavor_present": boolean, // subtle, grounded, human flavor (no clichés), in natural English
  "cliche_score": integer,        // 0 (none) .. 5 (heavy clichés)
  "corporate_jargon_score": integer, // 0..5 based on jargon tone (not exact words)
  "needs_revision": boolean,      // true if the text feels generic/boardroom, lacks flavor, or violates contract
  "reason": string
}}

<STYLE_CONTRACT>
{contract_text}
</STYLE_CONTRACT>

<TEXT>
{text}
</TEXT>
"""
    try:
        data = llm.chat_json(
            model="anthropic/claude-3-haiku",
            messages=[
                {"role": "system", "content": "You are a strict style auditor. Respond with strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Style audit failed: {e}")
        return {}


def revise_for_style(text: str, contract_text: str, hint: str = "") -> str:
    """Rewrite the text to satisfy contract with subtle local flavor in natural English, without clichés or Spanish."""
    user = f"""
Rewrite the text to satisfy the style contract with a subtle, human local flavor in natural English.
- Preserve the same core insight.
- 2–4 short paragraphs (separated by a blank line).
- No hashtags, emojis, or quotes. English only.
- Avoid corporate tone and clichés. Sound like a person at a bar, not a boardroom.
{('Hints: ' + hint) if hint else ''}

Return ONLY the rewritten text (no commentary).

TEXT:
{text}
"""
    try:
        out = llm.chat_text(
            model="anthropic/claude-3.5-sonnet",
            messages=[
                {"role": "system", "content": "You are a world-class ghostwriter. Follow the style contract strictly.\n\n<STYLE_CONTRACT>\n" + contract_text + "\n</STYLE_CONTRACT>"},
                {"role": "user", "content": user},
            ],
            temperature=0.8,
        )
        return out.strip()
    except Exception:
        return text


def improve_style(text: str, contract_text: str, rounds: int = STYLE_REVISION_ROUNDS) -> Tuple[str, Dict[str, Any]]:
    """Audit and optionally revise for style. Returns (text, last_audit)."""
    if not ENFORCE_STYLE_AUDIT:
        return text, {}

    # Heuristic quick checks
    jargon_hits = _detect_corporate_jargon(text)
    style_sim = _style_similarity_to_memory(text)

    audit = audit_style(text, contract_text)
    needs = False
    if isinstance(audit, dict):
        needs = bool(audit.get("needs_revision", False)) or not bool(audit.get("english_only", True))
        # Encourage revision if too corporate/jargon/cliché
        if int(audit.get("corporate_jargon_score", 0)) >= 3 or int(audit.get("cliche_score", 0)) >= 3:
            needs = True

    if jargon_hits >= 2:
        needs = True

    # If memory is available and similarity too low, nudge a revision
    if style_sim < 0.35 and get_memory_collection().count() > 0:
        needs = True

    revised = text
    while needs and rounds > 0:
        rounds -= 1
        hint = audit.get("reason", "") if isinstance(audit, dict) else ""
        revised = revise_for_style(revised, contract_text, hint=hint)
        audit = audit_style(revised, contract_text)
        needs = bool(audit.get("needs_revision", False)) if isinstance(audit, dict) else False

    return revised, (audit if isinstance(audit, dict) else {})
