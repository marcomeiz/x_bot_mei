import os
from typing import Dict, Any, Tuple, List, Optional

from dotenv import load_dotenv

from logger_config import logger
from llm_fallback import llm
from embeddings_manager import get_embedding, get_memory_collection
from persona import get_style_contract_text, get_final_guidelines_text
from src.prompt_loader import load_prompt
from src.settings import AppSettings


load_dotenv()

CONTRACT_TEXT = get_style_contract_text()
FINAL_GUIDELINES_TEXT = get_final_guidelines_text()


class StyleRejection(Exception):
    """Raised when a draft fails the final style audit."""

    pass

ENFORCE_STYLE_AUDIT = os.getenv("ENFORCE_STYLE_AUDIT", "1").lower() in ("1", "true", "yes", "y")
# Subimos la revisión por defecto para reforzar el tono humano sin cambiar interfaces
STYLE_REVISION_ROUNDS = int(os.getenv("STYLE_REVISION_ROUNDS", "2"))

# Umbrales configurables (defensas adicionales)
# Si aparece lenguaje dubitativo (hedging) >= umbral → revisar
STYLE_HEDGING_THRESHOLD = int(os.getenv("STYLE_HEDGING_THRESHOLD", "1") or 1)
# Si aparece jerga corporativa detectada localmente >= umbral → revisar
STYLE_JARGON_BLOCK_THRESHOLD = int(os.getenv("STYLE_JARGON_BLOCK_THRESHOLD", "1") or 1)
# Si el auditor LLM marca puntajes altos, gatillar revisión
STYLE_AUDIT_JARGON_SCORE_MIN = int(os.getenv("STYLE_AUDIT_JARGON_SCORE_MIN", "2") or 2)
STYLE_AUDIT_CLICHE_SCORE_MIN = int(os.getenv("STYLE_AUDIT_CLICHE_SCORE_MIN", "2") or 2)


_JARGON_LIST = {
    # Corporate/business cliches that flatten the tone
    "synergy", "paradigm", "stakeholder", "deliverables", "leverage",
    "empower", "optimize", "optimization", "enablement", "alignment",
    "ecosystem", "best practices", "bandwidth", "low-hanging fruit",
    "wheelhouse", "move the needle", "game changer", "thought leadership",
    "unlock value", "utilize", "enable", "value proposition", "roadmap",
    "north star", "mission-critical", "best-in-class", "future-proof",
    "top-down", "scalable", "framework", "streamline", "stakeholders",
}


def _detect_corporate_jargon(text: str) -> int:
    t = text.lower()
    hits = 0
    for w in _JARGON_LIST:
        if w in t:
            hits += 1
    return hits


# Hedging/downtoning (voz poco decidida)
_HEDGING = {
    "seems", "maybe", "might", "could", "perhaps", "probably",
    "i think", "i believe", "i guess", "appears", "likely",
    "try to", "aim to", "strive to", "in order to", "should",
}


def _detect_hedging(text: str) -> int:
    t = text.lower()
    return sum(1 for h in _HEDGING if h in t)


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
  "addresses_icp": boolean,       // speaks directly to the ICP with clear relevance
  "micro_win_present": boolean,   // contains a clear tactical next step (micro-win)
  "cliche_context": "allowed"|"blocked", // allow sharp phrasing if it amplifies authority without sounding like a poster
  "needs_revision": boolean,      // true if the text feels generic/boardroom, lacks flavor, or violates contract
  "reason": string
}}

<STYLE_CONTRACT>
{contract_text}
</STYLE_CONTRACT>

<FINAL_REVIEW_GUIDELINES>
{FINAL_GUIDELINES_TEXT}
</FINAL_REVIEW_GUIDELINES>

<TEXT>
{text}
</TEXT>
"""
    try:
        data = llm.chat_json(
            model=os.getenv("COMMENT_AUDIT_MODEL", "qwen/qwen-2.5-7b-instruct"),
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


def _heuristic_label_sections(lines: List[str]) -> Dict[str, Optional[int]]:
    """Heuristic fallback for labeling core sections without LLM.
    Returns indices for core_truth, hammer, contrast, imperative (or None).
    """
    import re as _re
    n = len(lines)
    if n == 0:
        return {"core_truth_idx": None, "hammer_idx": None, "contrast_idx": None, "imperative_idx": None}
    hammer_idx = n - 1
    core_truth_idx = 0
    contrast_idx: Optional[int] = None
    imperative_idx: Optional[int] = None

    # Detect imperative: start with strong verb or ends with '!'
    imperative_re = _re.compile(r"^(stop|start|set|charge|raise|lower|build|do|fix|delete|close|ship|post|sell|buy|pay|cut|keep|make|use)\b",
                                _re.IGNORECASE)
    contrast_re = _re.compile(r"\b(vs|but|instead)\b|\bdon't\b|\bdo\s+this\b|\bnot\b", _re.IGNORECASE)
    truth_re = _re.compile(r"\b(the real|truth|math|you (are|\'re) not|stop)\b", _re.IGNORECASE)

    for idx, ln in enumerate(lines):
        if imperative_idx is None and (ln.endswith("!") or imperative_re.search(ln)):
            imperative_idx = idx
        if contrast_idx is None and contrast_re.search(ln):
            contrast_idx = idx
        if truth_re.search(ln):
            core_truth_idx = idx

    return {
        "core_truth_idx": core_truth_idx,
        "hammer_idx": hammer_idx,
        "contrast_idx": contrast_idx,
        "imperative_idx": imperative_idx,
    }


def label_sections(text: str, contract_text: str) -> Dict[str, Any]:
    """Label core sections in a tweet-like text.

    Returns a JSON-like dict:
    {
      "core_truth_idx": int|null,
      "hammer_idx": int|null,
      "contrast_idx": int|null,
      "imperative_idx": int|null,
      "preserve_indices": [int, ...]
    }
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return {"core_truth_idx": None, "hammer_idx": None, "contrast_idx": None, "imperative_idx": None, "preserve_indices": []}

    prompt = f"""
Label the following lines with the indices of:
- core_truth: the uncomfortable truth / diagnosis that frames the problem
- hammer: the final decisive punch line
- contrast: the line that exposes the gap (wrong vs right / before vs after)
- imperative: the mandatory action (explicit imperative)

Return strict JSON with fields:
{{
  "core_truth_idx": integer|null,
  "hammer_idx": integer|null,
  "contrast_idx": integer|null,
  "imperative_idx": integer|null
}}

LINES (0-based):
{chr(10).join(f"[{i}] {l}" for i, l in enumerate(lines))}
"""
    try:
        data = llm.chat_json(
            model=os.getenv("COMMENT_AUDIT_MODEL", "qwen/qwen-2.5-7b-instruct"),
            messages=[
                {"role": "system", "content": (
                    "You precisely label core sections. Respond with strict JSON only. "
                    "Use the style contract for interpretation but do not rewrite.\n\n"
                    "<STYLE_CONTRACT>\n" + contract_text + "\n</STYLE_CONTRACT>"
                )},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        if not isinstance(data, dict):
            raise ValueError("Invalid labeler response")
        core_truth_idx = data.get("core_truth_idx")
        hammer_idx = data.get("hammer_idx")
        contrast_idx = data.get("contrast_idx")
        imperative_idx = data.get("imperative_idx")
    except Exception:
        heur = _heuristic_label_sections(lines)
        core_truth_idx = heur.get("core_truth_idx")
        hammer_idx = heur.get("hammer_idx")
        contrast_idx = heur.get("contrast_idx")
        imperative_idx = heur.get("imperative_idx")

    preserve = []
    for v in (core_truth_idx, hammer_idx):
        if isinstance(v, int) and 0 <= v < len(lines):
            preserve.append(v)
    return {
        "core_truth_idx": core_truth_idx if isinstance(core_truth_idx, int) else None,
        "hammer_idx": hammer_idx if isinstance(hammer_idx, int) else None,
        "contrast_idx": contrast_idx if isinstance(contrast_idx, int) else None,
        "imperative_idx": imperative_idx if isinstance(imperative_idx, int) else None,
        "preserve_indices": preserve,
    }


def revise_for_style(text: str, contract_text: str, hint: str = "", mode: str | None = None) -> str:
    """Rewrite the text to satisfy contract with subtle local flavor in natural English, without clichés or Spanish, and add punch."""
    tweet_rules = (
        "- Output as tweet lines: one sentence per line; each line ends with . ! or ?.\n"
        "- 5–12 words per line (strict).\n"
        "- No commas; if you need a pause, split into a new line.\n"
        "- Do NOT use the phrase 'and/or' (or 'y/o').\n"
        "- Keep total under 280 characters. Close with a single hammer line.\n"
    )
    extra = ("\n" + tweet_rules) if (mode == "tweet") else ""
    user = f"""
Rewrite the text to satisfy the style contract with a subtle, human local flavor in natural English — and sharper punch.{extra}
- Preserve the same core insight.
- Open with a punchy line (no hedging, no "Most people…").
- Add one concrete image or tactical detail (micro‑visual) to ground it.
- No hashtags, emojis, or quotes. English only.
- Avoid corporate tone and clichés. Sound like a person at a bar, not a boardroom.
- Short sentences. Strong verbs. Cut qualifiers (seems, maybe, might).
{('Hints: ' + hint) if hint else ''}

Return ONLY the rewritten text (no commentary).

TEXT:
{text}
"""
    try:
        out = llm.chat_text(
            model=os.getenv("COMMENT_REWRITE_MODEL", "mistralai/mistral-nemo"),
            messages=[
                {"role": "system", "content": (
                    "You are a world-class ghostwriter. Follow the style contract strictly. "
                    "Complement with the final review guidelines without contradicting the contract or ICP.\n\n"
                    "<STYLE_CONTRACT>\n" + contract_text + "\n</STYLE_CONTRACT>\n\n"
                    "<FINAL_REVIEW_GUIDELINES>\n" + FINAL_GUIDELINES_TEXT + "\n</FINAL_REVIEW_GUIDELINES>"
                )},
                {"role": "user", "content": user},
            ],
            temperature=0.8,
        )
        return out.strip()
    except Exception:
        return text


def improve_style(text: str, contract_text: str, rounds: int = STYLE_REVISION_ROUNDS, *, mode: str | None = None) -> Tuple[str, Dict[str, Any]]:
    """Audit and optionally revise for style.

    Returns the improved text and the last audit payload when the draft clears the bar.
    Raises StyleRejection with feedback if the draft still fails the final review.
    """
    if not ENFORCE_STYLE_AUDIT:
        return text, {}

    # Heuristic quick checks
    jargon_hits = _detect_corporate_jargon(text)
    hedge_hits = _detect_hedging(text)
    style_sim = _style_similarity_to_memory(text)

    audit = audit_style(text, contract_text)
    needs = False
    if isinstance(audit, dict):
        needs = bool(audit.get("needs_revision", False)) or not bool(audit.get("english_only", True))
        # Endurecer: si la voz es "boardroom", forzar revisión
        if str(audit.get("voice", "")).lower() == "boardroom":
            needs = True
        # Endurecer por puntajes LLM
        if int(audit.get("corporate_jargon_score", 0)) >= STYLE_AUDIT_JARGON_SCORE_MIN:
            needs = True
        if int(audit.get("cliche_score", 0)) >= STYLE_AUDIT_CLICHE_SCORE_MIN:
            needs = True

    # Endurecer por heurísticos locales
    if jargon_hits >= STYLE_JARGON_BLOCK_THRESHOLD:
        needs = True
    if hedge_hits >= STYLE_HEDGING_THRESHOLD:
        needs = True

    # If memory is available and similarity too low, nudge a revision
    if style_sim < 0.35 and get_memory_collection().count() > 0:
        needs = True

    revised = text
    while needs and rounds > 0:
        rounds -= 1
        hint = audit.get("reason", "") if isinstance(audit, dict) else ""
        revised = revise_for_style(revised, contract_text, hint=hint, mode=mode)
        audit = audit_style(revised, contract_text)
        needs = bool(audit.get("needs_revision", False)) if isinstance(audit, dict) else False

        # Reaplicar heurísticos tras nueva revisión
        if not needs:
            j2 = _detect_corporate_jargon(revised)
            h2 = _detect_hedging(revised)
            if j2 >= STYLE_JARGON_BLOCK_THRESHOLD or h2 >= STYLE_HEDGING_THRESHOLD:
                needs = True

    final_audit = audit if isinstance(audit, dict) else {}

    # Final heuristics and stop-gap checks (warden mode)
    final_reasons = []

    def _append_reason(condition: bool, message: str):
        if condition and message:
            final_reasons.append(message.strip())

    _append_reason(needs, "Reached max revision rounds without satisfying audit")
    _append_reason(bool(final_audit.get("needs_revision")), final_audit.get("reason", "LLM audit flagged unresolved issues"))
    _append_reason(str(final_audit.get("voice", "")).lower() == "boardroom", "Voice drifted to boardroom")
    _append_reason(not bool(final_audit.get("english_only", True)), "Non-English fragment detected")
    # Voice-first gating signals
    if isinstance(final_audit, dict):
        if not bool(final_audit.get("addresses_icp", True)):
            _append_reason(True, "Does not address ICP directly")
        if not bool(final_audit.get("micro_win_present", True)):
            _append_reason(True, "Missing clear micro-win")
        if str(final_audit.get("cliche_context", "")).lower() == "blocked":
            _append_reason(True, "Cliché blocked by context")

    final_jargon = _detect_corporate_jargon(revised)
    final_hedge = _detect_hedging(revised)
    _append_reason(final_jargon >= STYLE_JARGON_BLOCK_THRESHOLD, "Corporate jargon persists")
    _append_reason(final_hedge >= STYLE_HEDGING_THRESHOLD, "Hedging detected")

    memory_available = False
    try:
        memory_available = get_memory_collection().count() > 0
    except Exception:
        memory_available = False

    if memory_available:
        try:
            final_style_sim = _style_similarity_to_memory(revised)
            if final_style_sim < 0.35:
                _append_reason(True, "Too dissimilar to approved memory")
        except Exception:
            pass

    if final_reasons:
        # Compose explicit feedback for the lead writer (join reasons, dedupe blanks)
        feedback = "; ".join(r for r in final_reasons if r)
        raise StyleRejection(feedback or "Draft rejected by final style audit")

    return revised, final_audit


def audit_and_improve_comment(comment: str, source_text: str, contract_text: str) -> Tuple[str, bool]:
    """
    Audits a comment against the "Accept and Connect" v4.0 protocol.
    If non-compliant, it attempts to rewrite it.

    Returns a tuple of (final_comment, was_rewritten).
    Raises StyleRejection if the comment is unsalvageable or fails post-rewrite.
    """
    try:
        prompts_dir = AppSettings.load().prompts_dir
        spec = load_prompt(prompts_dir, "comments/audit")
        prompt = spec.render(comment=comment, source_text=source_text)
    except Exception:
        prompt = f"""
You are a ruthless Style Compliance Officer. Your only job is to audit and, if necessary, correct a generated comment based on the "Accept and Connect" v4.0 protocol.

**Original Post (for context):**
---
{source_text}
---

**Generated Comment to Audit:**
---
{comment}
---

**The "Accept and Connect" v4.0 Checklist (Your Rules):**
1.  **Unambiguous Validation:** Does the comment start with a clear, enthusiastic agreement with the author's core idea?
2.  **No Contradiction:** Does the comment avoid replacing the author's terminology (e.g., saying "it's not X, it's Y")? It MUST connect to the author's term, not invalidate it.
3.  **Non-Confrontational Tone:** Is the tone 100% constructive and non-diagnostic towards the author? It must not sound like a correction.
4.  **"Trojan Horse" Integration:** Does it successfully integrate a core operational term (system, asset, bottleneck, etc.) naturally?

**Your Task:**
Return a strict JSON object with your findings.

- If the comment passes ALL checks, return:
  `{{"is_compliant": true, "reason": "Comment adheres to all v4.0 principles."}}`

- If the comment fails ANY check, return:
  `{{"is_compliant": false, "reason": "<Briefly explain the specific rule it broke>", "corrected_text": "<Rewrite the comment to be 100% compliant, preserving the core insight.>"}}`
"""
    try:
        data = llm.chat_json(
            model="anthropic/claude-3.5-sonnet",
            messages=[
                {"role": "system", "content": "You are a strict style auditor for comments. Respond with strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        if not isinstance(data, dict):
            raise StyleRejection("Auditor returned invalid data.")

        if data.get("is_compliant"):
            logger.info("Comment passed v4.0 audit.")
            return comment, False

        reason = data.get("reason", "No reason provided.")
        corrected_text = data.get("corrected_text", "").strip()

        if not corrected_text:
            raise StyleRejection(f"Comment failed audit and could not be corrected. Reason: {reason}")

        logger.warning("Comment failed v4.0 audit, but was corrected. Reason: %s", reason)
        return corrected_text, True

    except Exception as e:
        logger.error(f"Error during comment audit: {e}", exc_info=True)
        raise StyleRejection(f"Exception during comment audit: {e}")
