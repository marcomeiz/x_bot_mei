import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set

from logger_config import logger


DEFAULT_LEXICON: Dict[str, Sequence[str]] = {
    "banned_words": ("bueno", "bien", "solo", "entonces", "ya"),
    "banned_suffixes": ("mente",),
    "washed_adverbs": (
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
    ),
    "whitelist_ly": ("only", "daily", "early", "family", "reply", "supply", "apply", "friendly", "timely"),
    "stopwords": (
        "the",
        "and",
        "that",
        "with",
        "this",
        "from",
        "they",
        "have",
        "will",
        "your",
        "their",
        "about",
        "there",
        "what",
        "when",
        "where",
        "while",
        "would",
        "could",
        "should",
        "might",
        "into",
        "over",
        "under",
        "only",
        "just",
        "been",
        "being",
        "once",
        "also",
        "more",
        "less",
        "than",
        "then",
        "such",
        "even",
        "some",
        "most",
        "much",
        "very",
        "like",
        "felt",
        "them",
        "ours",
        "ourselves",
        "yours",
        "yourself",
        "myself",
        "hers",
        "herself",
        "himself",
        "itself",
        "each",
        "because",
        "which",
        "into",
        "onto",
        "among",
        "after",
        "before",
        "again",
        "between",
        "across",
        "around",
        "through",
        "every",
    ),
}


def _default_lexicon_path() -> Path:
    override = os.getenv("LEXICON_CONFIG_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "lexicon.json"


def _merge_sequence(target: List[str], source: Iterable[str]) -> None:
    seen = set(target)
    for item in source:
        if item not in seen:
            target.append(item)
            seen.add(item)


@lru_cache(maxsize=1)
def _load_lexicon() -> Dict[str, List[str]]:
    base = {key: list(value) for key, value in DEFAULT_LEXICON.items()}
    path = _default_lexicon_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Root JSON must be an object.")
        for key, default_values in DEFAULT_LEXICON.items():
            custom = data.get(key)
            if not custom:
                continue
            if not isinstance(custom, list):
                logger.warning("Lexicon key '%s' in %s must be a list. Ignoring.", key, path)
                continue
            _merge_sequence(base[key], custom)
    except FileNotFoundError:
        logger.info("Lexicon config not found at %s. Using defaults.", path)
    except Exception as exc:
        logger.warning("Failed to load lexicon config from %s: %s. Using defaults.", path, exc)
    return base


def get_banned_words() -> Set[str]:
    return set(_load_lexicon()["banned_words"])


def get_banned_suffixes() -> tuple:
    values = _load_lexicon()["banned_suffixes"]
    return tuple(values)


def get_washed_adverbs() -> Set[str]:
    return set(_load_lexicon()["washed_adverbs"])


def get_whitelist_ly() -> Set[str]:
    return set(_load_lexicon()["whitelist_ly"])


def get_stopwords() -> Set[str]:
    return set(_load_lexicon()["stopwords"])
