import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field, ValidationError


_FRONT_MATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)\n---\s*\n?", re.MULTILINE)


class PromptSpec(BaseModel):
    """Serializable prompt specification loaded from .md/.yaml files.

    Expected for .md files with YAML front‑matter:
    ---
    id: generation/all_variants
    purpose: One‑shot multi‑variant generation
    inputs: [topic_abstract]
    constraints:
      - json_only
      - under_280
    model_hints:
      temperature: 0.6
    ---
    <template body with {placeholders}>
    """

    id: str = Field(..., description="Unique id within the prompts tree")
    purpose: Optional[str] = Field(default=None)
    template: str = Field(..., description="Prompt template with {placeholders}")
    inputs: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    model_hints: Dict[str, object] = Field(default_factory=dict)

    def render(self, **kwargs) -> str:
        missing = [name for name in self.inputs if name not in kwargs]
        if missing:
            raise ValueError(f"Missing inputs for prompt '{self.id}': {', '.join(missing)}")
        # Escape braces in values to avoid f-string like formatting issues downstream.
        safe_kwargs = {k: str(v).replace("{", "{{").replace("}", "}}") for k, v in kwargs.items()}
        try:
            return self.template.format(**safe_kwargs)
        except KeyError as e:
            raise ValueError(f"Template placeholders not provided: {e}")


def _split_front_matter(text: str) -> Tuple[Dict[str, object], str]:
    """Return (meta, body) from a Markdown file with optional YAML front‑matter."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta_raw = m.group(1)
    body = text[m.end() :]
    try:
        meta = yaml.safe_load(meta_raw) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    return meta, body


def load_prompt_file(path: str, *, prompt_id: Optional[str] = None) -> PromptSpec:
    """Load a .md prompt with YAML front‑matter or a .yaml with fields.

    - .md: parse front‑matter and body as template
    - .yaml: expect keys {id, template, inputs?, constraints?, model_hints?}
    """
    _, ext = os.path.splitext(path)
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()

    if ext.lower() in {".md", ".mdx"}:
        meta, body = _split_front_matter(content)
        if not body.strip():
            raise ValueError(f"Prompt body empty in {path}")
        data = {
            "id": prompt_id or meta.get("id") or os.path.splitext(os.path.basename(path))[0],
            "purpose": meta.get("purpose"),
            "inputs": list(meta.get("inputs", []) or []),
            "constraints": list(meta.get("constraints", []) or []),
            "model_hints": dict(meta.get("model_hints", {}) or {}),
            "template": body.strip(),
        }
        try:
            return PromptSpec(**data)
        except ValidationError as e:
            raise ValueError(f"Invalid prompt spec in {path}: {e}")

    if ext.lower() in {".yaml", ".yml"}:
        try:
            data = yaml.safe_load(content) or {}
        except Exception as e:
            raise ValueError(f"Invalid YAML in {path}: {e}")
        if not isinstance(data, dict):
            raise ValueError(f"YAML root must be a mapping in {path}")
        data.setdefault("id", prompt_id or os.path.splitext(os.path.basename(path))[0])
        if "template" not in data:
            raise ValueError(f"Missing 'template' in {path}")
        return PromptSpec(**data)

    raise ValueError(f"Unsupported prompt file extension: {ext}")


def find_prompt(base_dir: str, rel_id: str) -> str:
    """Resolve a prompt id to a file path within base_dir.

    Resolution order:
    - {rel_id}.md
    - {rel_id}.yaml
    - {rel_id}/index.md
    - {rel_id}/index.yaml
    """
    candidates = [
        os.path.join(base_dir, f"{rel_id}.md"),
        os.path.join(base_dir, f"{rel_id}.yaml"),
        os.path.join(base_dir, rel_id, "index.md"),
        os.path.join(base_dir, rel_id, "index.yaml"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"Prompt '{rel_id}' not found under {base_dir}")


def load_prompt(base_dir: str, rel_id: str) -> PromptSpec:
    """Load PromptSpec by logical id relative to a prompts directory."""
    path = find_prompt(base_dir, rel_id)
    return load_prompt_file(path, prompt_id=rel_id)

