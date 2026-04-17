# 009 — Tool System & Sub-Agent Permission/Protection Bypass

**Date**: 2026-04-17
**Severity**: Critical (security), Medium (robustness)
**Status**: Fixed
**Files Modified**:
- `opencode/session/prompt.py`
- `opencode/session/processor.py`
- `opencode/tool/task.py`
- `opencode/tool/batch.py`
- `opencode/tool/base.py`
- `opencode/server/app.py`
- `opencode/server/routes/session.py`
- `opencode/server/routes/permission.py`
- `opencode/permission/permission.py`
- `web/src/hooks/usePermission.ts`

---

## Summary

A comprehensive audit of the tool system, sub-agent system, and frontend permission flow revealed **8 issues** spanning the entire stack. The most critical: the permission system was completely non-functional end-to-end — backend never enforced rules, the server never wired the manager, and the frontend could never receive permission requests.

---

## Issues Found & Fixes Applied

### 1. [CRITICAL] `permission_manager` Never Wired into Processor

**Location**: `opencode/session/prompt.py` — `ProcessorContext` construction

**Problem**: `ProcessorContext.permission_manager` defaulted to `None` and was never set when creating the context in `prompt()`. The permission check in `processor.py:191` (`if ctx.permission_manager:`) was therefore always skipped. The entire permission system — including agent-specific rules like "explore agent can only use read-only tools" — was completely inert.

**Fix**: 
- Import `PermissionManager` and `Rule` into `prompt.py`
- `prompt()` now accepts an optional `permission_manager` parameter
- When not provided (CLI/headless), creates a local `PermissionManager(bus, project_id=session_id)`
- When provided (HTTP server), uses the shared instance so the frontend can reply
- Convert the agent's permission config dicts into `Rule` objects
- Pass both `permission_manager` and `agent_permission` (as `list[Rule]`) to `ProcessorContext`

**Impact**: All agent permission rules are now enforced in the main agentic loop.

---

### 2. [CRITICAL] Task Tool Bypassed All Permission Checks

**Location**: `opencode/tool/task.py` — inner tool execution loop

**Problem**: The task tool (sub-agent spawner) directly called `tool_impl.execute()` without any permission checking. This meant:
- The `explore` agent could execute `write`/`edit`/`bash` despite its permission rules restricting it to read-only tools
- No agent-level access control was enforced for sub-agent tool calls

**Fix**:
- Added `_build_agent_ruleset()` to convert agent permission config to `Rule` objects
- Added `_check_tool_permission()` that evaluates each tool call against the agent's ruleset
- Permission rules with `action=ask` are treated as denied in sub-agent context (no interactive user available)
- Denied tool calls return an error message to the sub-agent LLM, allowing it to adapt

---

### 3. [CRITICAL] Frontend-Backend Permission Link Was Broken

**Location**: `opencode/server/app.py`, `opencode/server/routes/session.py`, `opencode/server/routes/permission.py`

**Problem**: The entire frontend ↔ backend permission chain was disconnected:

1. **`app.py` never called `permission.set_manager()`** — the route's `_manager` was always `None`
2. **`GET /permission`** always returned `[]` (no manager to query)
3. **`POST /permission/:id`** always returned `{"ok": false, "error": "Permission manager not initialized"}`
4. **`prompt()` created isolated PermissionManagers** that the frontend could never reach
5. **Reply type mismatch**: frontend sent `"allow"`, backend expected `"once"`

Even if a tool hit an "ask" rule, the permission request would hang forever because no one could reply.

**Fix**:
- `app.py`: Creates a shared `PermissionManager(bus, project_id="global")`, calls `permission.set_manager()`, stores on `app.state`
- `session.py`: Retrieves `perm_manager` from `request.app.state` and passes it to `prompt()`
- `prompt.py`: Accepts optional `permission_manager` parameter — uses shared instance when provided
- `permission.py` route: Maps frontend `"allow"` to backend `"once"` in reply handler
- `permission.py` model: Fixed duplicate `self._lock` assignment

---

### 4. [MEDIUM] Frontend Used Inefficient Polling

**Location**: `web/src/hooks/usePermission.ts`

**Problem**: The frontend polled `GET /permission` every 1 second. This was:
- Wasteful (394 out of 395 polls return `[]`)
- Slow (up to 1 second latency before showing the permission modal)
- The backend already had SSE event infrastructure (`/event`) and published `permission.asked` / `permission.replied` events

**Fix**: Rewrote `usePermission` hook to:
- Use SSE via `EventSource('/event?event_type=*')` for real-time `permission.asked` / `permission.replied` events
- Fall back to 2-second polling only if SSE connection fails
- Auto-reconnect and re-poll on SSE recovery to avoid missed events

---

### 5. [MEDIUM] Batch Tool Bypassed All Protections

**Location**: `opencode/tool/batch.py`

**Problem**: Similar to the task tool, the batch tool directly called `tool_impl.execute()` without permission checks, doom loop detection, or cache recording.

**Fix**:
- Added permission evaluation using `eval_permission()` during the validation phase
- Tool calls that fail permission checks are now rejected before execution
- Supports receiving agent ruleset for permission enforcement

---

### 6. [MEDIUM] Sub-Agent Had No Loop Guard Protection

**Location**: `opencode/tool/task.py`

**Problem**: The sub-agent loop only had a simple `MAX_TURNS = 8` counter. It lacked:
- Pattern detection (repeated identical calls)
- Ping-pong detection (A→B→A→B alternation)
- Stall detection (same output repeated)
- Tool result caching (read-only deduplication)
- Cache invalidation on writes

**Fix**:
- Initialize a dedicated `LoopGuard` per sub-agent invocation with tuned config:
  - `max_iterations=8`, `repeat_threshold=3`, `stall_threshold=3`
  - Cache enabled with `max_size=50`
- Guard check runs before each turn; stops loop on pattern detection
- All tool calls recorded via `guard.record_tool_call()` for pattern analysis
- Successful read-only results cached; mutating tools trigger cache invalidation
- Cache hits skip tool execution entirely

---

### 7. [MEDIUM] Sub-Agent Returned `ToolOk` on LLM Errors

**Location**: `opencode/tool/task.py:120-124`

**Problem**: When the LLM stream emitted an `ErrorEvent`, the sub-agent returned `ToolOk(...)`. This caused the main loop to treat the failure as success, preventing doom loop detection from triggering.

**Fix**: Changed to return `ToolError(...)` so the main loop correctly identifies the failure.

---

### 8. [LOW] `ToolResultBuilder` Truncation Counter Inaccurate

**Location**: `opencode/tool/base.py:148-156`

**Problem**: When output truncation occurred, `self._total` was not updated with the actual number of characters written (the `remaining` portion). The truncation message displayed an inaccurate character count.

**Fix**: Added `self._total += remaining` after writing the truncated portion. Updated the truncation message to say "wrote N chars" for clarity.

---

## Type Safety Improvements

**Location**: `opencode/session/processor.py`

Changed `ProcessorContext` field types from `Any` to proper types:
```python
# Before
permission_manager: Any = None
agent_permission: list[dict[str, Any]] = field(default_factory=list)

# After  
permission_manager: PermissionManager | None = None
agent_permission: list[Rule] = field(default_factory=list)
```

**Location**: `opencode/permission/permission.py`

Removed duplicate `self._lock = asyncio.Lock()` line.

---

## Verification

- **394 tests pass** (all existing tests unaffected)
- **Lint clean** on all modified files (`ruff check` passes)
- Pre-existing B023 warnings in `prompt.py` (loop variable binding) are unrelated and unchanged

---

## Architecture: Full Permission Flow (After Fix)

### HTTP Server (Frontend-Driven)

```
User sends message via frontend
  → POST /session/:id/message
    → session route retrieves shared PermissionManager from app.state
    → prompt(inp, bus, permission_manager=shared_pm)
      → ProcessorContext(permission_manager=shared_pm, agent_permission=ruleset)
        → processor.py: for each tool call
          → shared_pm.ask(permission=tool_name, ruleset=agent_ruleset)
            → evaluate.py: last-matching-rule-wins
              → "allow": proceed
              → "deny": raise DeniedError
              → "ask": publish PERMISSION_ASKED to bus → await future
                        ↓ (SSE event stream)
                        Frontend receives permission.asked event
                        → PermissionModal shown to user
                        → User clicks allow/reject/always
                        → POST /permission/:id { reply: "allow" }
                        → shared_pm.reply() resolves future
                        → tool execution resumes
```

### CLI (Terminal-Driven)

```
User sends message via terminal
  → prompt(inp, bus)  [no permission_manager passed]
    → creates local PermissionManager(bus, session_id)
    → same evaluation flow, but "ask" rules publish to local bus
    → CLI can subscribe to bus events for terminal-based approval (future)
    → Currently "ask" rules block indefinitely in CLI (to be improved)
```

### Sub-Agent (Non-Interactive)

```
Task tool invoked
  → build agent ruleset from agent.permission config
  → for each sub-agent tool call:
    → _check_tool_permission(tool_name, ruleset)
      → eval_permission(): last-matching-rule-wins
        → "allow": execute tool
        → "deny"/"ask": return error message to sub-agent LLM
```

Key difference: sub-agents treat `action=ask` as denial because there is no interactive user session to prompt for approval.

### Sub-Agent Loop Guard

Each sub-agent now has its own `LoopGuard` instance with:
- **Pattern detection**: stops on 3 repeated identical calls, 3 stalled iterations
- **Result cache**: deduplicates read-only tool calls within the sub-agent session
- **Cache invalidation**: mutating tools (`edit`, `write`, `bash`) clear the cache
- **Turn-level guard check**: evaluated before each LLM call
