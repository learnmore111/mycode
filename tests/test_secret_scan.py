"""Tests for util.secret_scan."""

from __future__ import annotations

from mycode.util.secret_scan import format_report, has_critical, scan_text


def test_detects_openai_and_anthropic_keys():
    text = (
        "OPENAI_API_KEY = 'sk-proj-abcdef1234567890abcdef1234567890'\n"
        "ANTHROPIC=sk-ant-1234567890ABCDEF1234567890ABCDEF\n"
    )
    hits = scan_text(text)
    rules = {h.rule for h in hits}
    assert "openai_key" in rules
    assert "anthropic_key" in rules
    assert has_critical(hits)


def test_detects_aws_and_github_tokens():
    text = (
        "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n"
        "GH_TOKEN=ghp_abcdefghijklmnopqrstuvwx1234567890\n"
    )
    hits = scan_text(text)
    rules = {h.rule for h in hits}
    assert "aws_access_key_id" in rules
    assert "github_token" in rules


def test_private_key_block_is_critical():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAA...\n-----END RSA PRIVATE KEY-----\n"
    hits = scan_text(block)
    assert any(h.rule == "private_key_block" and h.severity == "critical" for h in hits)


def test_clean_content_has_no_hits():
    text = "print('hello world')\ndef add(a, b):\n    return a + b\n"
    assert scan_text(text) == []


def test_report_redacts_secret_in_sample():
    text = "token = sk-proj-abcdef1234567890abcdef1234567890"
    hits = scan_text(text)
    assert hits
    for h in hits:
        assert "sk-proj-abcdef" not in h.sample
        assert "***" in h.sample
    report = format_report(hits, path="config.py")
    assert "config.py" in report


def test_line_numbers_are_one_based():
    text = "\n\nsk-abcdefghijklmnopqrst\n"
    hits = scan_text(text)
    assert hits
    assert hits[0].line == 3
