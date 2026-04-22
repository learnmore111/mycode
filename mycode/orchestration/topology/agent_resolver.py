"""Resolve :class:`AgentSpec` (from a flow file) into a concrete ``AgentInfo``.

Bridges :mod:`mycode.orchestration.topology` (flow-level declarative spec)
and :mod:`mycode.orchestration.registry.agent_registry` (canonical agent
definitions) so an orchestration flow can reference a registry agent via
``extends`` and inline-override any subset of fields.

Merge semantics mirror :func:`agent_registry._merge_agents`:

- Fields explicitly set on ``AgentSpec`` override the parent; unset fields
  inherit.  Because ``AgentSpec`` is a Pydantic model we use
  ``model_fields_set`` to distinguish "unset" from "set to default".
- ``tools``: replaces parent when explicitly set.
- ``permission``: parent rules first, then child's (earlier rules win in
  the evaluator).
- ``disallowed_tools``: subtracted from the resulting tool allow-list (if
  any) so an extending agent can narrow the parent's toolset without
  re-declaring the full list.
- Identity fields (``name``, ``source``, ``source_path``, ``native``)
  always come from the resolved child.

This module intentionally does **not** depend on any runtime (processor,
subagent, etc.); it only produces a fully-resolved :class:`AgentInfo` that
the runtime can consume.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from mycode.agent.agent import AgentInfo
from mycode.orchestration.registry.agent_registry import (
    AgentLoadError,
    _merge_agents,
)

if TYPE_CHECKING:
    from mycode.orchestration.registry.agent_registry import AgentRegistry
    from mycode.orchestration.topology.schema import AgentSpec, PermissionRule


class AgentResolveError(ValueError):
    """Raised when an :class:`AgentSpec` cannot be resolved against the registry."""


# --- spec → AgentInfo ------------------------------------------------------


def _permission_rule_to_dict(rule: PermissionRule) -> dict[str, Any]:
    """Normalise a flow-level permission rule to the dict shape AgentInfo uses."""
    return {
        "permission": rule.permission,
        "pattern": rule.pattern,
        "action": rule.action,
    }


def _coerce_model(value: str | None) -> dict[str, str] | None:
    """Accept ``'providerID/modelID'`` string → dict form, or return None."""
    if not value:
        return None
    parts = value.split("/", 1)
    if len(parts) != 2:
        raise AgentResolveError(
            f"agent model must be 'providerID/modelID', got {value!r}"
        )
    return {"providerID": parts[0], "modelID": parts[1]}


def _spec_to_child_info(spec: AgentSpec) -> AgentInfo:
    """Build a raw ``AgentInfo`` from the *child-only* fields of ``spec``.

    The returned info carries only what was declared on the spec; no parent
    inheritance has happened yet.  This is merged against the resolved
    parent in :func:`resolve_agent_spec`.
    """
    return AgentInfo(
        name=spec.name,
        description=spec.description or "",
        mode="all",  # overridden on merge if parent specifies otherwise
        native=False,
        prompt=spec.prompt,
        temperature=spec.temperature,
        top_p=spec.top_p,
        model=_coerce_model(spec.model),
        permission=[_permission_rule_to_dict(r) for r in spec.permission],
        role=spec.role,
        tools=list(spec.tools) if spec.tools is not None else None,
        extends=spec.extends,
        max_turns=spec.max_turns,
        isolation=spec.isolation if spec.isolation in ("none", "worktree", "container") else "none",
        omit_claudemd=spec.omit_claudemd,
        source="project",  # flow specs are treated as project-layer overrides
    )


# Mapping AgentSpec field name → AgentInfo field name (only where they differ
# or where "being set on spec" should mark the info field as explicit).
_SPEC_TO_INFO_FIELDS: dict[str, str] = {
    "description": "description",
    "role": "role",
    "prompt": "prompt",
    "model": "model",
    "temperature": "temperature",
    "top_p": "top_p",
    "tools": "tools",
    "permission": "permission",
    "isolation": "isolation",
    "max_turns": "max_turns",
    "omit_claudemd": "omit_claudemd",
}


def _explicit_info_fields(spec: AgentSpec) -> set[str]:
    """Return the set of ``AgentInfo`` fields that were explicitly declared on ``spec``.

    Pydantic's ``model_fields_set`` only contains keys that were present in
    the source YAML/JSON, so default values do **not** count as "explicit".
    """
    touched = spec.model_fields_set
    explicit: set[str] = set()
    for spec_field, info_field in _SPEC_TO_INFO_FIELDS.items():
        if spec_field in touched:
            explicit.add(info_field)
    return explicit


# --- public API ------------------------------------------------------------


def resolve_agent_spec(
    spec: AgentSpec,
    registry: AgentRegistry,
    *,
    fallback_agent: str | None = None,
) -> AgentInfo:
    """Resolve one :class:`AgentSpec` into a concrete :class:`AgentInfo`.

    Steps:
      1. If ``spec.extends`` is set, resolve it through ``registry`` (which
         itself honours its own extends chain).
      2. Otherwise, if the spec's name already exists in the registry, use
         that as the implicit parent (so a flow can "reopen" a registry
         agent and tweak a few fields without redeclaring ``extends``).
      3. Otherwise, if ``fallback_agent`` is provided and exists in the
         registry, use it as the parent.  This is handy for stages that
         declare an inline coordinator without an explicit parent.
      4. If no parent is found, the spec must stand on its own (prompt +
         tools must be sufficient).  We synthesize a minimal ``AgentInfo``
         in that case.

    Then :func:`_merge_agents` layers the child on top of the parent using
    the precise *explicit-fields* set from the Pydantic spec, producing the
    final ``AgentInfo``.

    After merge, ``disallowed_tools`` (if any) is subtracted from the
    resulting ``tools`` list.
    """
    child = _spec_to_child_info(spec)
    explicit = _explicit_info_fields(spec)

    parent: AgentInfo | None = None
    parent_name: str | None = spec.extends

    if parent_name:
        try:
            parent = registry.resolve(parent_name)
        except KeyError as exc:
            raise AgentResolveError(
                f"agent {spec.name!r}: extends references unknown agent {parent_name!r}"
            ) from exc
        except AgentLoadError as exc:
            raise AgentResolveError(
                f"agent {spec.name!r}: failed to resolve parent {parent_name!r}: {exc}"
            ) from exc
    else:
        # Implicit parent: same name in registry (e.g. a flow tweaking "build").
        try:
            parent = registry.resolve(spec.name)
        except KeyError:
            parent = None

        if parent is None and fallback_agent:
            try:
                parent = registry.resolve(fallback_agent)
            except KeyError:
                parent = None

    if parent is None:
        # No parent — child must be self-sufficient.
        merged = copy.copy(child)
    else:
        merged = _merge_agents(parent, child, child_explicit=explicit)
        # Preserve the child's identity metadata.
        merged.name = spec.name
        merged.source = "project"  # type: ignore[assignment]
        merged.source_path = None
        merged.extends = parent_name  # remember the declared link

    # Apply disallowed_tools as a post-filter on the merged allow-list.
    if spec.disallowed_tools and merged.tools:
        blocked = set(spec.disallowed_tools)
        merged.tools = [t for t in merged.tools if t not in blocked]

    return merged


def resolve_all_agents(
    agents: list[AgentSpec],
    registry: AgentRegistry,
    *,
    fallback_agent: str | None = None,
) -> dict[str, AgentInfo]:
    """Resolve every ``AgentSpec`` in a flow into a ``{name: AgentInfo}`` map.

    Fails fast: raises :class:`AgentResolveError` on the first unresolvable
    spec so the caller sees a clean diagnostic rather than a half-merged
    topology.
    """
    out: dict[str, AgentInfo] = {}
    for spec in agents:
        info = resolve_agent_spec(spec, registry, fallback_agent=fallback_agent)
        out[info.name] = info
    return out
