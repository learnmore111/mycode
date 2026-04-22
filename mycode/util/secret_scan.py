"""Lightweight secret detector.

Scans arbitrary text for common credential patterns. Meant to be used
from:

* ``snapshot.track()`` — warn when we're about to commit secrets into
  the shadow-git repo.
* ``tool/bash.py`` output post-processing — redact obvious keys before
  the tool result is handed back to the LLM.
* ``tool/read.py`` — same, for files the agent is about to ingest.

We avoid a heavy dep like ``detect-secrets`` / ``gitleaks`` so this
module works offline and zero-install. Patterns cover the most common
shapes; we consciously err on the side of over-matching because every
``scan_text`` call returns a list the caller can decide what to do with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# (name, regex, severity) — severity ∈ {"critical", "high", "low"}.
_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "critical"),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"), "critical"),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "critical"),
    ("aws_access_key_id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "critical"),
    ("aws_secret_key", re.compile(
        r"(?i)aws(.{0,20})?(secret|sk)[^\n]{0,5}[\"'=: ][A-Za-z0-9/+=]{40}"),
        "critical"),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "critical"),
    ("slack_token", re.compile(r"\bxox[aboprs]-[A-Za-z0-9\-]{10,}\b"), "high"),
    ("stripe_key", re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{16,}\b"), "critical"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "high"),
    ("private_key_block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"), "critical"),
    # Key-like assignment: `SOMETHING_KEY = "xxxx..."`. Lower priority.
    ("generic_assignment",
     re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*[\"']?[A-Za-z0-9/+=_\-]{20,}[\"']?"),
     "low"),
)


@dataclass(frozen=True)
class SecretHit:
    """Single secret match in a scanned text."""
    rule: str
    severity: str
    line: int          # 1-based
    column: int        # 0-based, byte offset within the line
    sample: str        # redacted snippet for diagnostics (not the raw value)


def scan_text(text: str, *, path: str | None = None) -> list[SecretHit]:
    """Return every pattern hit in *text*.

    The returned ``sample`` is a truncated preview with the secret value
    itself replaced by ``***`` so logging the result list is safe. The
    ``path`` argument is only stored in the caller's error message — we
    do not emit it ourselves.
    """
    del path  # reserved for future weighting (e.g. skip vendored dirs)
    hits: list[SecretHit] = []
    # Precompute line offsets once so we can map absolute spans to line
    # numbers without a second full scan.
    line_offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_offsets.append(i + 1)
    for rule, pat, sev in _PATTERNS:
        for m in pat.finditer(text):
            start = m.start()
            # Binary search would be nicer; linear is fine for our sizes.
            line = 1
            for idx, offset in enumerate(line_offsets):
                if offset > start:
                    line = idx
                    break
            else:
                line = len(line_offsets)
            col = start - line_offsets[line - 1]
            # Build a redacted sample: up to 40 chars around the match
            # with the match itself replaced by "***".
            raw_sample = text[max(0, start - 10): m.end() + 10]
            safe_sample = raw_sample.replace(m.group(0), "***")
            hits.append(SecretHit(
                rule=rule, severity=sev, line=line, column=col, sample=safe_sample.strip(),
            ))
    return hits


def has_critical(hits: Iterable[SecretHit]) -> bool:
    return any(h.severity == "critical" for h in hits)


def format_report(hits: Iterable[SecretHit], *, path: str | None = None) -> str:
    """Human-readable summary suitable for CLI / log output."""
    items = list(hits)
    if not items:
        return "No secrets detected."
    header = f"{len(items)} potential secret(s) in {path}" if path else f"{len(items)} potential secret(s)"
    lines = [header, "-" * len(header)]
    for h in items:
        lines.append(f"  [{h.severity}] {h.rule} @ line {h.line}:{h.column} — {h.sample}")
    return "\n".join(lines)


__all__ = ["SecretHit", "scan_text", "has_critical", "format_report"]
