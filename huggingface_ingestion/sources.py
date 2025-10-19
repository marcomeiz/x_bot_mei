import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from logger_config import logger


@dataclass
class HuggingFaceSourceConfig:
    """Configuration for a Hugging Face dataset ingestion source."""

    name: str
    dataset: str
    split: str = "train"
    text_field: str = "text"
    id_field: str = ""
    metadata_fields: List[str] = field(default_factory=list)
    include_keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    stage: str = ""
    max_examples: int = 200
    min_length: int = 80
    max_length: int = 4096
    snippet_length: int = 480
    metadata: Dict[str, str] = field(default_factory=dict)
    data_files: Dict[str, str] = field(default_factory=dict)

    def should_include(self, text: str) -> bool:
        lowered = text.lower()
        if self.include_keywords:
            if not any(keyword.lower() in lowered for keyword in self.include_keywords):
                return False
        if any(keyword.lower() in lowered for keyword in self.exclude_keywords):
            return False
        length_ok = self.min_length <= len(lowered) <= self.max_length
        return length_ok

    def base_metadata(self) -> Dict[str, str]:
        data = {
            "hf_source": self.name,
            "hf_dataset": self.dataset,
            "hf_split": self.split,
            "tags": self.tags,
        }
        if self.stage:
            data["stage"] = self.stage
        data.update(self.metadata)
        return data


def _resolve_path(path: Optional[str]) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    env_path = os.getenv("HF_SOURCES_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "config" / "hf_sources.json"


def _normalise_entry(entry: Dict) -> Dict:
    entry = dict(entry)
    entry.setdefault("split", "train")
    entry.setdefault("text_field", "text")
    entry.setdefault("metadata_fields", [])
    entry.setdefault("include_keywords", [])
    entry.setdefault("exclude_keywords", [])
    entry.setdefault("tags", [])
    entry.setdefault("metadata", {})
    entry.setdefault("data_files", {})
    entry.setdefault("max_examples", 200)
    entry.setdefault("min_length", 80)
    entry.setdefault("max_length", 4096)
    entry.setdefault("snippet_length", 480)
    return entry


def load_sources_config(path: Optional[str] = None) -> List[HuggingFaceSourceConfig]:
    config_path = _resolve_path(path)
    if not config_path.exists():
        logger.info("No se encontró configuración de Hugging Face en %s.", config_path)
        return []

    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        logger.error("No se pudo leer configuración Hugging Face: %s", exc)
        return []

    if not isinstance(data, Iterable):
        logger.error("Formato inválido de configuración Hugging Face. Debe ser una lista.")
        return []

    configs: List[HuggingFaceSourceConfig] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        normalised = _normalise_entry(entry)
        try:
            configs.append(HuggingFaceSourceConfig(**normalised))
        except TypeError as exc:
            logger.warning("Entrada inválida en configuración Hugging Face (%s): %s", entry.get("name"), exc)
    return configs
