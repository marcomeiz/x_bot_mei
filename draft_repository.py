import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class DraftPayload:
    draft_a: Optional[str]
    draft_b: Optional[str]
    draft_c: Optional[str]
    category: Optional[str]

    def get_variant(self, option: str) -> Optional[str]:
        mapping = {
            "a": self.draft_a,
            "b": self.draft_b,
            "c": self.draft_c,
        }
        return mapping.get(option.lower())


class DraftRepository:
    """Persists proposal drafts per chat/topic in json files."""

    def __init__(self, base_dir: str) -> None:
        self.base_path = Path(base_dir)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: int, topic_id: str) -> Path:
        return self.base_path / f"{chat_id}_{topic_id}.json"

    def save(self, chat_id: int, topic_id: str, payload: DraftPayload) -> None:
        path = self._path(chat_id, topic_id)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(payload), handle)

    def load(self, chat_id: int, topic_id: str) -> DraftPayload:
        path = self._path(chat_id, topic_id)
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return DraftPayload(
            draft_a=data.get("draft_a", ""),
            draft_b=data.get("draft_b", ""),
            draft_c=data.get("draft_c"),
            category=data.get("category"),
        )

    def delete(self, chat_id: int, topic_id: str) -> None:
        path = self._path(chat_id, topic_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
