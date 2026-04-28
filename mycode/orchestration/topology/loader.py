"""Load orchestration files (.yaml / .yml / .json) into :class:`OrchestrationSpec`.

Features:

- JSON or YAML via ``yaml.safe_load`` (falls back to ``json.loads``)
- ``vars`` block + user overrides → rendered with a **safe** ``str.format_map``
  style replacement (supports ``{{ vars.foo }}`` and ``{{ foo }}``).
- ``extends: <flow_name>`` inheritance between orchestration files, resolved
  by the flow_registry (injected via ``parent_resolver`` to keep this module
  free of I/O dependencies).
- Returns a fully-validated :class:`OrchestrationSpec` (``pydantic.ValidationError``
  is re-raised as :class:`OrchestrationLoadError`).

We do **not** use Jinja2 to keep the dependency surface small; the renderer
supports ``{{ vars.KEY }}`` and ``{{ KEY }}`` with nested attribute access.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from mycode.orchestration.topology.schema import OrchestrationSpec

if TYPE_CHECKING:
    from collections.abc import Callable

# --- Errors ----------------------------------------------------------------


class OrchestrationLoadError(ValueError):
    """Raised when an orchestration file cannot be parsed or validated."""


# --- Public API ------------------------------------------------------------


def load_file(
    path: str | Path,
    *,
    vars_override: dict[str, Any] | None = None,
    parent_resolver: Callable[[str], OrchestrationSpec] | None = None,
) -> OrchestrationSpec:
    """Load an orchestration spec from a file.

    Args:
        path: path to .yaml / .yml / .json file.
        vars_override: user-supplied variable overrides (merged over ``vars``).
        parent_resolver: optional callable mapping a flow name → parent
            :class:`OrchestrationSpec`. Required when the file uses ``extends:``.
    """
    p = Path(path)
    if not p.is_file():
        raise OrchestrationLoadError(f"orchestration file not found: {p}")

    text = p.read_text(encoding="utf-8")
    suffix = p.suffix.lower()
    try:
        if suffix == ".json":
            raw: Any = json.loads(text)
        else:  # yaml is a superset of json, so default to yaml
            raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OrchestrationLoadError(f"YAML parse error in {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OrchestrationLoadError(f"JSON parse error in {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise OrchestrationLoadError(f"{p}: orchestration root must be a mapping, got {type(raw).__name__}")

    spec = load_mapping(raw, vars_override=vars_override, parent_resolver=parent_resolver)
    # Preserve source path for diagnostics / later file resolution
    spec = spec.model_copy(update={"source_path": str(p.resolve())})
    return spec


def load_mapping(
    data: dict[str, Any],
    *,
    vars_override: dict[str, Any] | None = None,
    parent_resolver: Callable[[str], OrchestrationSpec] | None = None,
) -> OrchestrationSpec:
    """Load an orchestration spec from an already-parsed dict.

    Responsibilities:
      1. Resolve ``extends`` (recursively) and deep-merge parent into child.
      2. Merge ``vars_override`` into ``vars``.
      3. Render ``{{ vars.KEY }}`` / ``{{ KEY }}`` across string fields.
      4. Validate through Pydantic.
    """
    if not isinstance(data, dict):
        raise OrchestrationLoadError(f"expected mapping, got {type(data).__name__}")

    merged = _resolve_extends(data, parent_resolver)

    # Merge vars: file < user_override
    file_vars: dict[str, Any] = dict(merged.get("vars") or {})
    if vars_override:
        file_vars.update(vars_override)
    merged["vars"] = file_vars

    # Render templated strings. We render a *copy* so the original dict is intact
    # for debugging tools that want to show "pre-render" form.
    rendered = render_variables(merged, file_vars)

    try:
        return OrchestrationSpec.model_validate(rendered)
    except ValidationError as exc:
        raise OrchestrationLoadError(f"orchestration validation failed: {exc}") from exc


# --- extends resolution ----------------------------------------------------


def _resolve_extends(
    data: dict[str, Any],
    parent_resolver: Callable[[str], OrchestrationSpec] | None,
    *,
    _depth: int = 0,
) -> dict[str, Any]:
    """Resolve the optional ``extends`` key by deep-merging the parent first."""
    if _depth > 5:
        raise OrchestrationLoadError("orchestration extends chain too deep (>5)")

    parent_name = data.get("extends")
    if not parent_name:
        return dict(data)

    if parent_resolver is None:
        raise OrchestrationLoadError(
            f"orchestration extends '{parent_name}' but no parent_resolver was provided"
        )

    parent_spec = parent_resolver(parent_name)
    # Serialize parent spec back to a dict so we can merge as raw data
    parent_dict = parent_spec.model_dump(exclude_none=True)
    # Parent may itself extend — recurse using its dump (which already includes
    # its inherited content, so no further recursion is needed, but we guard).
    parent_dict.pop("extends", None)

    child = {k: v for k, v in data.items() if k != "extends"}
    return _deep_merge(parent_dict, child)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` into ``base``.

    Rules:
      - dict × dict → recurse
      - list of dicts (with ``name`` or ``id`` key) × list → merge-by-key
      - anything else in ``override`` replaces ``base``
    """
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out and isinstance(out[k], list) and isinstance(v, list):
            out[k] = _merge_keyed_list(out[k], v)
        else:
            out[k] = v
    return out


def _merge_keyed_list(base: list[Any], override: list[Any]) -> list[Any]:
    """Merge two lists. If both contain dicts with a shared key (``name`` or
    ``id``), merge items with matching key. Otherwise concat-replace (override
    wins when non-keyed)."""
    key = _guess_list_key(base) or _guess_list_key(override)
    if key is None:
        return list(override)  # full replacement semantics for primitive lists

    by_key: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for item in base:
        if isinstance(item, dict) and key in item:
            by_key[item[key]] = dict(item)
            order.append(item[key])
    for item in override:
        if not isinstance(item, dict) or key not in item:
            # Append primitive / non-keyed dicts
            order.append(_sentinel := object())
            by_key[id(_sentinel)] = item  # store under unique id
            continue
        k = item[key]
        if k in by_key:
            by_key[k] = _deep_merge(by_key[k], item)
        else:
            by_key[k] = dict(item)
            order.append(k)

    return [by_key[k] for k in order]


def _guess_list_key(items: list[Any]) -> str | None:
    for item in items:
        if isinstance(item, dict):
            if "name" in item:
                return "name"
            if "id" in item:
                return "id"
    return None


# --- template rendering ----------------------------------------------------

_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


def render_variables(data: Any, variables: dict[str, Any]) -> Any:
    """Recursively render ``{{ key.path }}`` placeholders in all string values.

    - ``{{ vars.foo }}`` is equivalent to ``{{ foo }}``; both look up in
      ``variables``. The ``vars.`` prefix is just a convention for readability.
    - Missing keys are left verbatim (``{{ unknown }}``) so validators can
      detect them.
    """
    if isinstance(data, dict):
        return {k: render_variables(v, variables) for k, v in data.items()}
    if isinstance(data, list):
        return [render_variables(v, variables) for v in data]
    if isinstance(data, str):
        return _render_str(data, variables)
    return data


def _render_str(s: str, variables: dict[str, Any]) -> str:
    def _sub(match: re.Match[str]) -> str:
        path = match.group(1)
        # Strip optional "vars." prefix
        if path.startswith("vars."):
            path = path[len("vars."):]
        parts = path.split(".")
        value: Any = variables
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return match.group(0)  # leave unchanged
        return str(value)

    return _TEMPLATE_RE.sub(_sub, s)
