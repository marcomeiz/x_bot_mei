from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

# --------------------------------------------------------------------------- Hooks


@dataclass(frozen=True)
class HookGuideline:
    name: str
    description: str
    sample: str


HOOK_GUIDELINES: Tuple[HookGuideline, ...] = (
    HookGuideline(
        name="pain",
        description="Open with the raw cost, tension, or loss the reader is living.",
        sample='“I came up short. Story of my life…”',
    ),
    HookGuideline(
        name="vulnerability",
        description="Confess a flaw or mistake in plain language to earn trust.",
        sample='"I almost quit. Not because I was broke — because I was bored."',
    ),
    HookGuideline(
        name="controversy",
        description="State the punchy opinion others avoid saying out loud.",
        sample='"I cheat like a mofo on Threads…" (and immediately explain the tactic).',
    ),
    HookGuideline(
        name="credibility",
        description="Lead with a proof point (wins, scars, reps) that shows you’ve been there.",
        sample='"250 ops calls later, this is the pattern that still scares me."',
    ),
    HookGuideline(
        name="curiosity",
        description="Pose a sharp curiosity gap that makes the reader need the second sentence.",
        sample='"My best ops habit came from the worst founder I ever shadowed."',
    ),
    HookGuideline(
        name="radical_change",
        description="Signal a drastic before/after the reader wants to copy (or avoid).",
        sample='"Use Threads to make money, not argue with strangers."',
    ),
)


def hook_menu() -> str:
    lines = [
        "- Hooks (choose one and show it in the first 3 words):",
    ]
    for hook in HOOK_GUIDELINES:
        lines.append(f"  * {hook.name}: {hook.description} Example: {hook.sample}")
    return "\n".join(lines)


# ------------------------------------------------------------------------ Formats


@dataclass(frozen=True)
class FormatProfile:
    name: str
    description: str
    instructions: str
    mandatory: bool = True


FORMAT_PROFILES: Dict[str, FormatProfile] = {
    "stair_up": FormatProfile(
        name="stair_up",
        description="Three sentences where each line grows in length (6-9 words, 10-16 words, 16-22 words).",
        instructions=(
            "- Format: three sentences in a staircase. Line 1 = 6-9 words. "
            "Line 2 = 10-16 words. Line 3 = 16-22 words. No commas; each sentence stands alone.\n"
            "- Escalate tension: each sentence raises the stakes or detail.\n"
        ),
        mandatory=False,
    ),
    "stair_down": FormatProfile(
        name="stair_down",
        description="Start long, finish with a short punch (22-16-8 words).",
        instructions=(
            "- Format: three sentences descending. Line 1 = 18-24 words. "
            "Line 2 = 12-16 words. Line 3 = 6-9 words. No commas.\n"
            "- Use the final short line as the hammer.\n"
        ),
        mandatory=False,
    ),
    "staccato": FormatProfile(
        name="staccato",
        description="Sequence of hard, short sentences (max 8 words) that feel like body blows.",
        instructions=(
            "- Format: 3-5 sentences, each <=8 words. No commas. No conjunctions.\n"
            "- Every line must be drawable — concrete action or object.\n"
        ),
        mandatory=True,
    ),
    "list_strikes": FormatProfile(
        name="list_strikes",
        description="Bullet-like strikes separated by periods; reads like a mini playbook.",
        instructions=(
            "- Format: 4 bullet strikes separated by periods (no bullet symbols). "
            "Each strike <=12 words, actionable, drawable, no conjunctions.\n"
            "- The last strike must be the inspirational hammer.\n"
        ),
        mandatory=True,
    ),
}


def select_format(rng: random.Random, variant: str) -> FormatProfile:
    pools: Dict[str, Iterable[str]] = {
        "A": ("stair_up", "staccato"),
        "B": ("stair_down", "staccato"),
        "C": ("list_strikes", "stair_up", "stair_down"),
        "comment": ("staccato", "stair_down"),
    }
    choices = tuple(pools.get(variant, FORMAT_PROFILES.keys()))
    key = rng.choice(choices)
    return FORMAT_PROFILES[key]


# ---------------------------------------------------------------------- Analogies


def should_allow_analogy(rng: random.Random) -> bool:
    """Return True roughly 1 out of 5 times."""
    return rng.random() < 0.2


# -------------------------------------------------------------------- Banned words

BANNED_WORDS = {
    "bueno",
    "bien",
    "solo",
    "entonces",
    "ya",
}

BANNED_SUFFIXES = (
    "mente",
)

# Focused list of washed adverbs that weaken punch. We do NOT ban all *-ly words.
WASHED_ADVERBS = {
    "really",
    "actually",
    "literally",
    "basically",
    "totally",
    "simply",
    "clearly",
    "obviously",
    "honestly",
    "quickly",
    "easily",
    "probably",
    "hopefully",
    "seriously",
    "highly",
    "extremely",
    "definitely",
    "absolutely",
}

# Common *-ly words we explicitly allow (to avoid false positives)
WHITELIST_LY = {"only", "daily", "early", "family", "reply", "supply", "apply", "friendly", "timely"}


def words_blocklist_prompt() -> str:
    return (
        "- Forbidden words: "
        + ", ".join(sorted(BANNED_WORDS))
        + ".\n- Avoid washed adverbs (really, actually, literally, basically, totally, simply, clearly, obviously, honestly, quickly, easily, probably, hopefully).\n"
        "- Never use adverbs ending in 'mente'.\n"
        "- Avoid generic adjectives; use concrete details instead.\n"
    )


def conjunction_guard_prompt() -> str:
    return "- Do not use the phrase 'and/or' (or 'y/o'). Prefer separate sentences.\n"


def comma_guard_prompt() -> str:
    return "- Avoid commas. If you need a pause, split into a new sentence.\n"


# ----------------------------------------------------------------- Visual anchors


def visual_anchor_prompt() -> str:
    return (
        "- Every line must describe something you can sketch: rooms, smells, body reactions, tools, timestamps.\n"
        "- Prefer verbs and objects over adjectives. Show the moment the COO can picture.\n"
    )


# ----------------------------------------------------------------------- Closers


def closing_rule_prompt() -> str:
    return "- Close with a single inspirational hammer line: decisive, no fluff, no cliché.\n"


# --------------------------------------------------------------- Validation utils

_WORD_REGEX = re.compile(r"[A-Za-z0-9']+")
_SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?])\s+")
_AND_OR_PHRASE_RE = re.compile(r"\b(?:and\s*/\s*or|y\s*/\s*o)\b", re.IGNORECASE)
_ANALOGY_PATTERNS = (
    re.compile(r"\slike\s", re.IGNORECASE),
    re.compile(r"\sas if\s", re.IGNORECASE),
    re.compile(r"\sas a\s", re.IGNORECASE),
    re.compile(r"\sas the\s", re.IGNORECASE),
)

_HASHTAG_RE = re.compile(r"#[A-Za-z0-9_]+")
_URL_RE = re.compile(r"(https?://\S+|\bwww\.[^\s]+)", re.IGNORECASE)
# Emoji and pictographs (basic ranges). Intentionally broad to catch most emojis.
_EMOJI_RE = re.compile(
    r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\u2600-\u26FF\u2700-\u27BF]",
    re.UNICODE,
)

# Common AI-ish/corporate patterns to block at the gate (heuristics)
_AI_PATTERN_STRINGS = (
    "most people",
    "in today's",
    "in todays",
    "let's dive",
    "ultimate guide",
    "game chang",
    "unlock",
    "synergy",
    "paradigm",
    "north star",
    "framework",
    "roadmap",
    "value proposition",
    "best practices",
    "leverage",
    "as an ai",
    "conclusion:",
    "tl;dr",
)
_AI_PATTERN_RES = tuple(re.compile(pat, re.IGNORECASE) for pat in _AI_PATTERN_STRINGS)


def split_sentences(text: str) -> List[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_REGEX.split(stripped) if s.strip()]


def word_count(sentence: str) -> int:
    return len(_WORD_REGEX.findall(sentence))


def detect_banned_elements(text: str) -> List[str]:
    issues: List[str] = []
    lower = text.lower()
    tokens = _WORD_REGEX.findall(lower)

    for banned in BANNED_WORDS:
        if banned in tokens:
            issues.append(f"contains banned word '{banned}'")

    suffix_hits = [
        token for token in tokens if any(token.endswith(suffix) for suffix in BANNED_SUFFIXES) and len(token) > 2
    ]
    if suffix_hits:
        issues.append("contains forbidden suffix words: " + ", ".join(sorted(set(suffix_hits))))

    # Focused adverb screen (skip whitelisted *-ly words)
    adverb_hits = [t for t in tokens if t in WASHED_ADVERBS and t not in WHITELIST_LY]
    if adverb_hits:
        issues.append("contains washed adverbs: " + ", ".join(sorted(set(adverb_hits))))

    if "," in text:
        issues.append("uses commas")

    if _AND_OR_PHRASE_RE.search(lower):
        issues.append("uses 'and/or' (or 'y/o') phrase")

    if _HASHTAG_RE.search(text):
        issues.append("contains hashtag")
    if _URL_RE.search(lower):
        issues.append("contains link")
    if _EMOJI_RE.search(text):
        issues.append("contains emoji")

    for rex in _AI_PATTERN_RES:
        if rex.search(lower):
            issues.append("contains ai-like pattern")
            break

    return issues


def count_analogy_markers(text: str) -> int:
    lowered = f" {text.lower()} "
    return sum(len(pattern.findall(lowered)) for pattern in _ANALOGY_PATTERNS)


def validate_format(text: str, profile: FormatProfile) -> Tuple[bool, str]:
    sentences = split_sentences(text)
    counts = [word_count(sentence) for sentence in sentences]

    if profile.name == "stair_up":
        if len(sentences) != 3:
            return False, "expected 3 sentences for staircase up"
        if not (6 <= counts[0] <= 9 and 10 <= counts[1] <= 16 and 16 <= counts[2] <= 22):
            return False, f"word counts off for stair_up ({counts})"
        if not (counts[0] < counts[1] < counts[2]):
            return False, "sentence lengths must increase"
        return True, ""

    if profile.name == "stair_down":
        if len(sentences) != 3:
            return False, "expected 3 sentences for staircase down"
        if not (18 <= counts[0] <= 24 and 12 <= counts[1] <= 16 and 6 <= counts[2] <= 9):
            return False, f"word counts off for stair_down ({counts})"
        if not (counts[0] > counts[1] > counts[2]):
            return False, "sentence lengths must decrease"
        return True, ""

    if profile.name == "staccato":
        if not (3 <= len(sentences) <= 5):
            return False, "staccato needs 3-5 sentences"
        if any(count > 8 for count in counts):
            return False, f"staccato sentence exceeds 8 words ({counts})"
        return True, ""

    if profile.name == "list_strikes":
        if len(sentences) != 4:
            return False, "list strikes need exactly 4 bullets"
        if any(count > 12 for count in counts):
            return False, f"list strike exceeds 12 words ({counts})"
        return True, ""

    return True, ""
