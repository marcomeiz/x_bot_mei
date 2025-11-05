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


def normalize_chroma_embeddings(raw: Any) -> List[List[float]]:
    """Normalize Chroma embeddings to a list of vectors.

    Supports common Chroma response shapes:
    - [[f1,f2,...], [g1,g2,...]]  → already list of vectors
    - [[[floats]], [[floats]]]     → double nesting from some clients; unwrap one level
    - [f1, f2, ...]                → single embedding returned; wrap as one vector
    Returns [] for empty or unsupported shapes.
    """
    if not isinstance(raw, list):
        return []
    if raw and isinstance(raw[0], list):
        # nested list case
        first = raw[0]
        if isinstance(first, list) and first and isinstance(first[0], (float, int)):
            # already list of float vectors
            return raw  # type: ignore[return-value]
        # double nesting: unwrap first level for each element
        return [e[0] if isinstance(e, list) else e for e in raw]  # type: ignore[list-item]
    if raw and isinstance(raw[0], (float, int)):
        # flat float list representing a single embedding
        return [raw]  # type: ignore[return-value]
    return []


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
