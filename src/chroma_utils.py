"""Shared helpers for dealing with ChromaDB nested results.

Chroma suele devolver campos con listas anidadas: [[ids], [docs]].
Estos utilitarios garantizan un aplanado consistente para reutilizarlos
desde los servicios que interactúan con la base vectorial.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Set


def flatten_chroma_array(raw: Any) -> List[Any]:
    """Return a flat list from Chroma responses that may be nested."""
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        flat: List[Any] = []
        for chunk in raw:
            if isinstance(chunk, list):
                flat.extend(chunk)
            else:
                flat.append(chunk)
        return flat
    return raw if isinstance(raw, list) else []


def flatten_chroma_ids(raw: Any) -> List[str]:
    """Ensure ids from Chroma end up as a flat list of strings."""
    return [str(item) for item in flatten_chroma_array(raw)]


def flatten_chroma_metadatas(raw: Any) -> List[Dict[str, Any]]:
    """Return metadata entries as dictionaries, falling back to {}."""
    flattened = []
    for item in flatten_chroma_array(raw):
        candidate = item
        if isinstance(candidate, list):
            candidate = candidate[0] if candidate else {}
        flattened.append(candidate if isinstance(candidate, dict) else {})
    return flattened


def get_existing_ids(collection: Any, ids: Sequence[str], *, log_warning: Any = None) -> Set[str]:
    """Retrieve the subset of `ids` that already exist in a Chroma collection."""
    if not ids:
        return set()
    try:
        response = collection.get(ids=list(ids), include=[])  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - defensive logging
        if log_warning:
            try:
                log_warning("No se pudo verificar duplicados en la colección: %s", exc)
            except Exception:
                pass
        return set()
    raw_ids = response.get("ids") if isinstance(response, dict) else None
    return set(flatten_chroma_ids(raw_ids))
