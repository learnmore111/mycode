"""Smoke tests for util.log redaction + util.metrics counters."""

from __future__ import annotations

from mycode.util import log as logmod
from mycode.util import metrics


def test_redaction_scrubs_bearer_tokens():
    scrubbed = logmod._scrub_string("Authorization: Bearer abc123def456ghi789jklmno")
    assert "abc123def456ghi789jklmno" not in scrubbed
    assert "***" in scrubbed


def test_redaction_scrubs_sk_prefixed_keys():
    for sample in (
        "Using sk-1234567890abcdefghij here",
        "key sk-ant-abcdefghij1234567890",
        "creds ghp_abcdefghijklmnop1234567890",
    ):
        scrubbed = logmod._scrub_string(sample)
        assert "***" in scrubbed


def test_redaction_replaces_home_dir():
    home_like = logmod._HOME_DIR or "/var/x"
    if not home_like or home_like == "/":
        # On the CI image HOME might be empty; skip in that case.
        return
    sample = f"Working in {home_like}/project/file.py"
    out = logmod._scrub_string(sample)
    assert home_like not in out
    assert "~" in out


def test_redaction_key_named_fields():
    # Structlog processor scrubs the *value* when the *key* is a known
    # secret-name, regardless of shape.
    event = {"event": "hi", "api_key": "sk-realbutshort", "token": "xxxxxxxxxxxxxxxxxxxx"}
    out = logmod._redact_processor(None, "info", dict(event))
    assert out["api_key"] == "***"
    assert out["token"] == "***"
    assert out["event"] == "hi"


def test_metrics_counter_and_snapshot():
    metrics.reset()
    metrics.counter("unit_test_counter", outcome="ok")
    metrics.counter("unit_test_counter", outcome="ok")
    metrics.counter("unit_test_counter", outcome="err")
    snap = metrics.snapshot()
    assert "unit_test_counter" in snap["counters"]
    rows = {tuple(sorted(entry["labels"].items())): entry["value"]
            for entry in snap["counters"]["unit_test_counter"]}
    assert rows[(("outcome", "ok"),)] == 2
    assert rows[(("outcome", "err"),)] == 1


def test_metrics_histogram_summary():
    metrics.reset()
    for v in (0.1, 0.2, 0.3, 0.4, 0.5):
        metrics.observe("unit_test_latency", v)
    snap = metrics.snapshot()
    hist = snap["histograms"]["unit_test_latency"]
    assert hist["count"] == 5
    assert hist["min"] == 0.1
    assert hist["max"] == 0.5
    assert 0.2 <= hist["mean"] <= 0.4
