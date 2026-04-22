"""AgentRegistry — discovers and resolves agent definitions from multiple sources.

Sources (later entries override earlier ones on name conflict):

1. **builtin** — hard-coded agents defined in ``mycode.agent.agent._build_agents``.
2. **config**  — ``agent:`` map inside ``mycode.json`` / project config.
3. **global**  — Markdown files in ``~/.mycode/agents/*.md`` (frontmatter + body).
4. **project** — Markdown files in ``<project>/.mycode/agents/*.md``.

File format (Markdown + YAML frontmatter)::

    ---
    description: Code reviewer agent
    mode: subagent
    role: reviewer
    extends: explore
    tools: [read, grep, glob]
    max_turns: 20
    isolation: none
    omit_claudemd: false
    temperature: 0.2
    color: yellow
    model: anthropic/claude-sonnet-4-5
    permission:
      - { permission: edit, pattern: "*", action: deny }
    ---
    You are a code reviewer.  Focus on correctness, readability, tests.

The body (after the closing ``---``) becomes the agent's ``prompt``.

The registry is explicitly **decoupled** from the ``agent.agent`` module —
it returns ``AgentInfo`` objects but does not mutate the cached agent dict.
Integration with ``agent._build_all_agents`` will be done in a later
milestone; for M2 the registry stands alone and is exercised via CLI.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from mycode.agent.agent import AgentInfo, _build_agents
from mycode.config import config as configmod

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable


class AgentLoadError(Exception):
    """Raised when an agent definition file cannot be parsed."""


# --- Frontmatter parsing ---------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into (frontmatter_dict, body).

    Accepts files with or without frontmatter.  Frontmatter must start on the
    very first line with ``---`` and end with another ``---``.  If no
    frontmatter is present the entire text becomes the body.
    """
    if not text.startswith("---"):
        return {}, text.strip()

    lines = text.splitlines()
    # Find the closing '---' (allowing trailing whitespace).
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        # Unterminated frontmatter — treat whole document as body.
        return {}, text.strip()

    header = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :]).strip()

    try:
        data = yaml.safe_load(header) or {}
    except yaml.YAMLError as exc:
        raise AgentLoadError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentLoadError("frontmatter must be a mapping")
    return data, body


def _coerce_model(value: Any) -> dict[str, str] | None:
    """Accept either ``'provider/model'`` string or ``{providerID, modelID}`` dict."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = value.split("/", 1)
        if len(parts) != 2:
            raise AgentLoadError(
                f"model must be in 'providerID/modelID' form, got {value!r}"
            )
        return {"providerID": parts[0], "modelID": parts[1]}
    if isinstance(value, dict):
        if "providerID" not in value or "modelID" not in value:
            raise AgentLoadError(
                "model dict must contain 'providerID' and 'modelID'"
            )
        return {"providerID": str(value["providerID"]), "modelID": str(value["modelID"])}
    raise AgentLoadError(f"unsupported model value: {value!r}")


def agentinfo_from_frontmatter(
    name: str,
    data: dict[str, Any],
    body: str,
    *,
    source: str,
    source_path: str | None = None,
) -> AgentInfo:
    """Construct an ``AgentInfo`` from parsed frontmatter + body prompt.

    The set of frontmatter keys that were *explicitly* provided is stashed
    on ``info.options['_explicit_keys']`` so the merge step can distinguish
    "unset" from "set to default".
    """

    allowed_keys = {
        "description", "mode", "hidden", "prompt", "temperature", "top_p",
        "color", "model", "variant", "permission", "options", "steps",
        "role", "tools", "extends", "max_turns", "isolation", "omit_claudemd",
    }
    unknown = set(data.keys()) - allowed_keys
    if unknown:
        raise AgentLoadError(
            f"unknown frontmatter fields: {sorted(unknown)}"
        )

    mode = data.get("mode", "all")
    if mode not in ("subagent", "primary", "all"):
        raise AgentLoadError(f"invalid mode: {mode!r}")

    isolation = data.get("isolation", "none")
    if isolation not in ("none", "worktree", "container"):
        raise AgentLoadError(f"invalid isolation: {isolation!r}")

    tools = data.get("tools")
    if tools is not None and (not isinstance(tools, list) or not all(isinstance(t, str) for t in tools)):
        raise AgentLoadError("tools must be a list of strings")

    permission = data.get("permission") or []
    if not isinstance(permission, list):
        raise AgentLoadError("permission must be a list of rule objects")

    prompt = data.get("prompt")
    if prompt is None and body:
        prompt = body

    return AgentInfo(
        name=name,
        description=str(data.get("description") or ""),
        mode=mode,
        native=False,
        hidden=bool(data.get("hidden") or False),
        prompt=prompt,
        temperature=data.get("temperature"),
        top_p=data.get("top_p"),
        color=data.get("color"),
        model=_coerce_model(data.get("model")),
        variant=data.get("variant"),
        permission=list(permission),
        options=dict(data.get("options") or {}),
        steps=data.get("steps"),
        role=data.get("role"),
        tools=list(tools) if tools else None,
        extends=data.get("extends"),
        max_turns=data.get("max_turns"),
        isolation=isolation,
        omit_claudemd=bool(data.get("omit_claudemd") or False),
        source=source,  # type: ignore[arg-type]
        source_path=source_path,
    )


def _explicit_keys_for(data: dict[str, Any], body: str) -> set[str]:
    """Return the set of frontmatter keys that were explicitly written."""
    keys = set(data.keys())
    if body and "prompt" not in keys:
        keys.add("prompt")
    return keys


# --- Merge / extends -------------------------------------------------------


_SENTINEL_KEEP_LIST: tuple[str, ...] = ("permission",)


def _merge_agents(
    parent: AgentInfo,
    child: AgentInfo,
    *,
    child_explicit: set[str] | None = None,
) -> AgentInfo:
    """Produce a new ``AgentInfo`` where ``child`` overrides ``parent``.

    Merge rules:
      - Scalar fields: child overrides when the field was explicitly set
        (per ``child_explicit``).  Otherwise the parent value is inherited.
        When ``child_explicit`` is ``None`` the function falls back to
        "non-None value wins" semantics (for programmatic callers).
      - ``permission``: concatenated — parent rules first, then child's
        (earlier rules take precedence in the evaluator).
      - ``options``: shallow-merged dict (child overrides parent keys).
      - ``tools``: child replaces parent entirely when set (non-None).
      - Identity fields (``name``, ``native``, ``source``, ``source_path``)
        always come from ``child``.
    """
    merged = replace(child)

    scalar_fields = (
        "description", "mode", "hidden", "prompt", "temperature", "top_p",
        "color", "model", "variant", "steps", "role", "max_turns",
        "isolation", "omit_claudemd",
    )
    for f in scalar_fields:
        child_val = getattr(child, f)
        if child_explicit is None:
            # Programmatic fallback: any "unset-looking" value inherits parent.
            parent_has_val = getattr(parent, f) not in (None, "", False)
            if child_val in (None, "", False) and parent_has_val and (
                f in {"hidden", "omit_claudemd"} or child_val in (None, "")
            ):
                setattr(merged, f, getattr(parent, f))
        elif f not in child_explicit:
            setattr(merged, f, getattr(parent, f))

    # tools: child replaces when explicitly set; otherwise inherit from parent.
    if child.tools is None:
        merged.tools = list(parent.tools) if parent.tools else None

    # permission: parent first, child appended.
    merged.permission = list(parent.permission) + list(child.permission)

    # options: parent ∘ child (child wins per-key).
    merged_options = dict(parent.options or {})
    merged_options.update(child.options or {})
    merged.options = merged_options

    return merged


# --- Registry --------------------------------------------------------------


@dataclass
class AgentSourceEntry:
    """A discovered agent definition awaiting resolution."""

    name: str
    info: AgentInfo
    source: str  # builtin / config / global / project
    source_path: str | None = None
    # Set of frontmatter keys explicitly supplied (for precise extends merging).
    # None means "treat everything as explicit" (built-in / config agents).
    explicit_keys: set[str] | None = None


class AgentRegistry:
    """Discovers + resolves agents from builtin / config / global / project."""

    def __init__(
        self,
        *,
        project_dir: str | os.PathLike[str] | None = None,
        global_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir) if project_dir else None
        self.global_dir = (
            Path(global_dir) if global_dir else Path.home() / ".mycode" / "agents"
        )
        self._entries: dict[str, AgentSourceEntry] | None = None

    # --- discovery -------------------------------------------------------

    def _discover_file_agents(
        self,
        root: Path,
        *,
        source: str,
    ) -> Iterable[AgentSourceEntry]:
        if not root.is_dir():
            return []
        out: list[AgentSourceEntry] = []
        for p in sorted(root.glob("*.md")):
            name = p.stem
            try:
                text = p.read_text(encoding="utf-8")
                data, body = parse_frontmatter(text)
                info = agentinfo_from_frontmatter(
                    name, data, body, source=source, source_path=str(p)
                )
                explicit = _explicit_keys_for(data, body)
            except AgentLoadError as exc:
                raise AgentLoadError(f"{p}: {exc}") from exc
            out.append(
                AgentSourceEntry(
                    name=name, info=info, source=source, source_path=str(p),
                    explicit_keys=explicit,
                )
            )
        return out

    def _discover_config_agents(self) -> Iterable[AgentSourceEntry]:
        cfg = configmod.get()
        out: list[AgentSourceEntry] = []
        for name, acfg in (cfg.agent or {}).items():
            if acfg.disable:
                continue  # handled in _build_all_agents path
            model = None
            if acfg.model:
                parts = acfg.model.split("/", 1)
                if len(parts) == 2:
                    model = {"providerID": parts[0], "modelID": parts[1]}
            info = AgentInfo(
                name=name,
                description=acfg.description or "",
                mode=(acfg.mode or "all"),
                native=False,
                hidden=bool(acfg.hidden) if acfg.hidden is not None else False,
                prompt=acfg.prompt,
                temperature=acfg.temperature,
                top_p=acfg.top_p,
                color=acfg.color,
                model=model,
                # PermissionConfig in mycode is a mapping-shaped schema, not
                # the list-of-rules form AgentInfo uses internally.  Skip for
                # now — M3 will unify these representations.
                permission=[],
                options=dict(acfg.options or {}),
                steps=acfg.steps,
                source="config",
            )
            out.append(AgentSourceEntry(name=name, info=info, source="config"))
        return out

    def _discover_builtin(self) -> Iterable[AgentSourceEntry]:
        out: list[AgentSourceEntry] = []
        for name, info in _build_agents().items():
            info_copy = copy.copy(info)
            info_copy.source = "builtin"  # type: ignore[assignment]
            out.append(AgentSourceEntry(name=name, info=info_copy, source="builtin"))
        return out

    def _load_all(self) -> dict[str, AgentSourceEntry]:
        if self._entries is not None:
            return self._entries

        entries: dict[str, AgentSourceEntry] = {}
        order = (
            ("builtin", self._discover_builtin()),
            ("config", self._discover_config_agents()),
            ("global", self._discover_file_agents(self.global_dir, source="global")),
        )
        if self.project_dir:
            proj_root = self.project_dir / ".mycode" / "agents"
            order = (*order, ("project", self._discover_file_agents(proj_root, source="project")))

        for _src, items in order:
            for e in items:
                entries[e.name] = e  # later source overrides earlier

        self._entries = entries
        return entries

    # --- public API ------------------------------------------------------

    def refresh(self) -> None:
        """Clear the internal cache so the next call re-discovers everything."""
        self._entries = None

    def list_entries(self) -> list[AgentSourceEntry]:
        """Return all discovered entries in source precedence order."""
        entries = self._load_all()
        # Stable ordering: source priority (builtin→config→global→project) then name.
        priority = {"builtin": 0, "config": 1, "global": 2, "project": 3}
        return sorted(entries.values(), key=lambda e: (priority.get(e.source, 99), e.name))

    def resolve(self, name: str, *, _seen: tuple[str, ...] | None = None) -> AgentInfo:
        """Resolve an agent by name, applying the ``extends`` chain."""
        entries = self._load_all()
        if name not in entries:
            raise KeyError(f"agent not found: {name!r}")

        seen = _seen or ()
        if name in seen:
            chain = " → ".join([*seen, name])
            raise AgentLoadError(f"extends cycle detected: {chain}")

        entry = entries[name]
        info = copy.copy(entry.info)

        parent_name = info.extends
        if parent_name:
            parent = self.resolve(parent_name, _seen=(*seen, name))
            info = _merge_agents(parent, info, child_explicit=entry.explicit_keys)
            # Preserve the child's identity metadata after merge.
            info.name = name
            info.source = entry.source  # type: ignore[assignment]
            info.source_path = entry.source_path

        return info

    def resolve_all(self) -> dict[str, AgentInfo]:
        """Resolve every registered agent (applies extends chains)."""
        return {e.name: self.resolve(e.name) for e in self.list_entries()}


# --- Module-level default registry ----------------------------------------


_default: AgentRegistry | None = None


def get_default_registry(
    *,
    project_dir: str | os.PathLike[str] | None = None,
    refresh: bool = False,
) -> AgentRegistry:
    """Return a process-wide default registry instance."""
    global _default
    if _default is None or refresh or (
        project_dir and str(project_dir) != str(_default.project_dir)
    ):
        _default = AgentRegistry(project_dir=project_dir)
    return _default
