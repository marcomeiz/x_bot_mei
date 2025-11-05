import os
from types import SimpleNamespace


def test_get_topic_or_fallback_returns_fallback_when_primary_none(monkeypatch):
    """If primary selection returns None, repo must return a fallback topic with source=fallback."""
    from src import topics_repo

    # Ensure deterministic behavior: primary returns None
    monkeypatch.setattr(topics_repo, "pick_for", lambda user_id: None)

    res = topics_repo.get_topic_or_fallback("user-123")
    assert isinstance(res, dict)
    assert res.get("source") == "fallback"
    assert isinstance(res.get("id"), str) and len(res.get("id")) > 0
    assert isinstance(res.get("text"), str) and len(res.get("text")) > 0


def test_get_topic_or_fallback_emits_capture_on_fallback(monkeypatch):
    """Repo should emit telemetry capture when falling back."""
    from src import topics_repo

    events = []

    def fake_safe_capture(event: str, payload: dict | None = None):
        events.append((event, payload or {}))

    # Patch primary to None and safe_capture to recorder
    monkeypatch.setattr(topics_repo, "pick_for", lambda user_id: None)
    monkeypatch.setattr(topics_repo, "safe_capture", fake_safe_capture)

    _ = topics_repo.get_topic_or_fallback("user-xyz")
    assert any(e[0] == "topic_fallback_used" for e in events), "Expected topic_fallback_used telemetry event"
    # Validate payload includes the user id
    matched = [p for (ev, p) in events if ev == "topic_fallback_used"]
    assert matched and matched[0].get("user") == "user-xyz"

