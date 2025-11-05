import os
import types
import pytest

from src.telemetry import TELEMETRY, Telemetry, safe_capture


class ExplodingClient:
    def capture(self, event, payload=None):
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset_env():
    # Ensure clean env per test
    os.environ.pop("TELEMETRY_STRICT", None)
    yield
    os.environ.pop("TELEMETRY_STRICT", None)


def test_safe_capture_swallows_errors_in_non_strict(monkeypatch, caplog):
    # Arrange: set exploding client
    monkeypatch.setattr("src.telemetry.TELEMETRY", Telemetry(client=ExplodingClient()))
    os.environ["TELEMETRY_STRICT"] = "0"

    # Act
    with caplog.at_level("WARNING"):
        safe_capture("TEST_EVENT", {"x": 1})

    # Assert: no exception, warning logged
    assert any("TELEM_CAPTURE_FAILED" in str(rec.message) for rec in caplog.records)


def test_safe_capture_raises_in_strict(monkeypatch, caplog):
    # Arrange: set exploding client
    monkeypatch.setattr("src.telemetry.TELEMETRY", Telemetry(client=ExplodingClient()))
    os.environ["TELEMETRY_STRICT"] = "1"

    # Act + Assert: exception raised
    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError):
            safe_capture("TEST_EVENT", {"x": 1})

    assert any("TELEM_CAPTURE_HARD_FAIL" in str(rec.message) for rec in caplog.records)

