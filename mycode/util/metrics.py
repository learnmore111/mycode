"""Lightweight in-process metrics and optional OpenTelemetry bridge.

Design goals:

* **Zero-cost when disabled.** No imports unless a counter is touched.
* **No hard dep on OTel.** If the ``opentelemetry`` package is installed we
  mirror counters / histograms to it; otherwise we only maintain in-memory
  tallies that can be scraped via ``snapshot()`` — good enough for tests
  and ``/health`` endpoints.
* **Thread-safe.** Counters use plain ints guarded by a module lock; the
  agent loop does not spawn many threads so contention is negligible.

Intended call sites (not yet wired up — adding hooks as we touch code):
  - ``llm_request_total{model, outcome}`` — stream() success / error tallies
  - ``tool_call_total{tool, outcome}`` — processor _run_tool
  - ``permission_ask_total{reply}`` — PermissionManager.reply
  - ``compaction_total`` — session.compaction

The module is safe to import from anywhere (util/ has no intra-package
dependencies) so wiring up is a single ``from mycode.util.metrics import
counter``.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

_lock = threading.Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
_histograms: dict[str, list[float]] = {}

# Lazy OTel hook. When the caller installs opentelemetry-api we mirror
# counter increments and histogram observations there.
_otel_counter: Any = None
_otel_histogram: Any = None
_otel_enabled = False


def _maybe_init_otel() -> None:
    global _otel_counter, _otel_histogram, _otel_enabled
    if _otel_enabled or _otel_counter is not None:
        return
    try:
        from opentelemetry import metrics  # type: ignore

        meter = metrics.get_meter("mycode")
        _otel_counter = meter.create_counter("mycode_counter")
        _otel_histogram = meter.create_histogram("mycode_histogram")
        _otel_enabled = True
    except Exception:
        # Absence of opentelemetry is fine — we keep in-memory only.
        _otel_enabled = False


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def counter(name: str, value: int = 1, **labels: str) -> None:
    """Increment a named counter.

    ``labels`` are passed through to OpenTelemetry as attributes when
    available. The in-memory key includes a sorted label tuple so
    ``counter("x", k="a")`` and ``counter("x", k="b")`` are distinct.
    """
    _maybe_init_otel()
    key = (name, _labels_key(labels))
    with _lock:
        _counters[key] = _counters.get(key, 0) + value
    if _otel_counter is not None:
        # Failures in the OTel pipeline must never break user-facing code —
        # metrics are best-effort.
        with contextlib.suppress(Exception):
            _otel_counter.add(value, {"name": name, **labels})


def observe(name: str, value: float, **labels: str) -> None:
    """Record a single histogram observation (e.g. latency in seconds)."""
    _maybe_init_otel()
    with _lock:
        _histograms.setdefault(name, []).append(value)
        # Cap per-name history so a long-running process does not balloon.
        if len(_histograms[name]) > 1000:
            _histograms[name] = _histograms[name][-1000:]
    if _otel_histogram is not None:
        with contextlib.suppress(Exception):
            _otel_histogram.record(value, {"name": name, **labels})


def snapshot() -> dict[str, Any]:
    """Return a point-in-time copy of counters + histogram summaries."""
    with _lock:
        counters_out: dict[str, list[dict[str, Any]]] = {}
        for (name, label_tuple), value in _counters.items():
            counters_out.setdefault(name, []).append({
                "labels": dict(label_tuple),
                "value": value,
            })
        hist_out: dict[str, dict[str, float]] = {}
        for name, values in _histograms.items():
            if not values:
                continue
            srt = sorted(values)
            n = len(srt)
            hist_out[name] = {
                "count": float(n),
                "min": srt[0],
                "max": srt[-1],
                "p50": srt[n // 2],
                "p95": srt[min(n - 1, int(n * 0.95))],
                "mean": sum(srt) / n,
            }
    return {"counters": counters_out, "histograms": hist_out, "otel": _otel_enabled}


def reset() -> None:
    """Drop all in-memory state. For tests only."""
    with _lock:
        _counters.clear()
        _histograms.clear()


# --- Tracing ---------------------------------------------------------------
#
# Same philosophy as counters: zero hard dep, zero-cost when no exporter is
# configured. We expose a single ``span()`` context manager that starts an
# OpenTelemetry span if ``opentelemetry-api`` is installed, otherwise records
# a duration into the histogram bucket named ``<name>_ms`` so ``snapshot()``
# still shows how much time was spent where.

_otel_tracer: Any = None
_otel_tracer_attempted = False


def _maybe_init_tracer() -> None:
    global _otel_tracer, _otel_tracer_attempted
    if _otel_tracer_attempted:
        return
    _otel_tracer_attempted = True
    try:
        from opentelemetry import trace  # type: ignore

        _otel_tracer = trace.get_tracer("mycode")
    except Exception:
        _otel_tracer = None


@contextlib.contextmanager
def span(name: str, **attributes: Any):
    """Start a trace span named ``name`` for the wrapped block.

    Always records block duration as ``<name>_ms`` histogram so teams
    without OTel still see per-call latency in ``/metrics``. With OTel
    installed the block also emits a real span tagged with ``attributes``
    so it lines up with exported traces in Honeycomb / Tempo / Jaeger.
    """
    _maybe_init_tracer()
    start = time.perf_counter()
    if _otel_tracer is None:
        try:
            yield
        finally:
            observe(f"{name}_ms", (time.perf_counter() - start) * 1000)
        return

    with _otel_tracer.start_as_current_span(name) as otel_span:
        try:
            for k, v in attributes.items():
                with contextlib.suppress(Exception):
                    otel_span.set_attribute(k, v)
            yield
        except BaseException as exc:
            with contextlib.suppress(Exception):
                otel_span.record_exception(exc)
                otel_span.set_attribute("error", True)
            raise
        finally:
            observe(f"{name}_ms", (time.perf_counter() - start) * 1000)
