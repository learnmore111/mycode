# Agent System - Quick Reference

## Core Architecture Components

### 1. **Agents** (`mycode/agent/agent.py`)
- **AgentInfo**: Configuration object defining behavior, permissions, model choice
- **Built-in Agents**:
  - `build`: Primary, default, interactive
  - `plan`: Primary, read-only, safe exploration
  - `general`: Sub-agent, full capabilities except `todowrite`
  - `explore`: Sub-agent, read-only only (for fast searches)
  - `compaction`, `title`, `summary`: Hidden utility agents

- **Key Configuration**:
  ```python
  agent.mode = "primary" | "subagent" | "all"
  agent.permission = [{"permission": "tool_id", "pattern": "*", "action": "allow|deny|ask"}]
  agent.steps = 50  # Max iterations
  ```

### 2. **Processor Loop** (`mycode/session/processor.py`)
The core agentic loop that streams LLM output and executes tools:

**Flow**:
```
LLM Stream → Parse Tools → Preflight (permission, cache, doom check)
  → Separate into read-only (parallel) + mutating (sequential)
  → Execute with retry logic
  → Record to loop guard → yield events
```

**Key Event Types**:
- `text_delta`: Incremental LLM text
- `tool_start` / `tool_running` / `tool_done`: Tool execution
- `error`: Errors or permission denials
- `finish`: Iteration complete (`continue` or `stop`)

### 3. **Tool Capability System** (`mycode/tool/base.py`)
Every tool declares capabilities that control execution:

```python
def is_read_only(args) -> bool       # Can run in parallel, eligible for cache
def is_destructive(args) -> bool     # Irreversible operation
def is_concurrency_safe(args) -> bool # Can run parallel with other tools
def is_enabled() -> bool              # Feature flag
```

**Execution Strategy**:
- Read-only + concurrency-safe → **parallel** via `asyncio.gather()`
- Else → **sequential** (order matters for writes)

### 4. **Loop Guard** (`mycode/session/loop_guard.py`)
Three-layer protection against infinite loops:

| Layer | Check | Verdict |
|-------|-------|---------|
| 1. Hard Limit | `iteration >= max` | `FORCE_STOP` |
| 2. Patterns | Repeat / Ping-pong / Stall | `STOP` |
| 3. Near Limit | At 90%+ with no progress | `STOP` |

**Pattern Detection**:
- **Repeat**: Same tool + input 3+ times
- **Ping-pong**: Tools A↔B alternating 4+ times
- **Stall**: Same output repeated 5+ times

### 5. **Tool Result Cache** (part of loop_guard)
Content-addressable cache for read-only tools:

```python
cache.get(tool_name, input_dict) → output or None
cache.put(tool_name, input_dict, output)
cache.invalidate()  # On any write (bash, edit, write)
```

**Cacheable tools**: `read`, `glob`, `grep`, `listdir`, `webfetch`, `websearch`, `skill`

### 6. **Session Orchestration** (`mycode/session/prompt.py`)
Orchestrates the full agentic loop:

```
1. Acquire session lock (prevent concurrent processing)
2. Resolve model + agent
3. For each iteration:
   - guard.check() → continue/warn/stop?
   - Inject system reminders (skills, date, memory)
   - Call process_stream()
   - On "continue" result: build tool results & loop
   - On "stop" result: finish
4. Persist to database
5. Release lock
```

### 7. **Permission System**
Each agent has a ruleset of permission rules:

```python
permission = [
    {"permission": "read", "pattern": "*.env", "action": "ask"},
    {"permission": "bash", "pattern": "*", "action": "deny"},
    {"permission": "*", "pattern": "*", "action": "allow"},
]
```

**Actions**:
- `allow`: Proceed
- `deny`: Blocked with error
- `ask`: Ask user (raises error for sub-agents)

---

## Task Tool: Agent Delegation

The **`task` tool** spawns a sub-agent with independent loop (max 8 turns):

```python
task(
    description="Search for TypeScript interfaces in src/",
    agent="explore"  # or "general"
)
```

**Features**:
- Own message history, loop guard, cache
- Respects agent's permission ruleset
- Can be aborted via `ctx.abort` (asyncio.Event)
- Max 8 turns (not 50) to prevent runaway

---

## Tool Types & Patterns

### Read-Only (Cacheable, Parallelizable)
- `read`: Read file
- `glob`: Find files
- `grep`: Search content
- `listdir`: List directory
- `webfetch`: Fetch web page
- `websearch`: Web search
- `skill`: Load skill file

### Mutating (Sequential, Cache-Invalidating)
- `bash`: Run command
- `write`: Write file
- `edit`: Edit file

### Delegation
- `task`: Spawn sub-agent (8-turn limit)
- `todo`: Task tracking

### Interaction
- `question`: Ask user

---

## Creating Custom Tools

```python
from pydantic import BaseModel
from mycode.tool.base import CallableTool, ToolContext, ToolOk, ToolError, ToolResult

class MyParams(BaseModel):
    query: str
    limit: int = 10

class MyTool(CallableTool[MyParams]):
    id = "my_tool"
    description = "Does something useful"
    
    def is_read_only(self, args=None) -> bool:
        return True  # or False
    
    def is_concurrency_safe(self, args=None) -> bool:
        return True  # or False
    
    async def call(self, params: MyParams, ctx: ToolContext) -> ToolResult:
        try:
            result = await do_work(params.query, params.limit)
            return ToolOk(
                output=result,
                title="My Tool Result",
                metadata={"items": len(result)}
            )
        except Exception as e:
            return ToolError(f"Error: {e}", title="My Tool Error")

# Register
from mycode.tool.registry import register
register(MyTool())
```

---

## Creating Custom Agents

In `config.yaml`:

```yaml
agent:
  my-agent:
    description: "Custom agent for specific tasks"
    mode: "subagent"  # or "primary" or "all"
    model: "openai/gpt-4"
    temperature: 0.7
    top_p: 0.9
    steps: 100  # Max iterations
    prompt: |
      You are a specialized agent for data analysis.
      Focus on accuracy and clarity.
    permission:
      - permission: "read"
        pattern: "data/**"
        action: "allow"
      - permission: "bash"
        pattern: "*"
        action: "allow"
      - permission: "write"
        pattern: "results/**"
        action: "allow"
      - permission: "*"
        pattern: "*"
        action: "deny"
```

Or programmatically:
```python
from mycode.agent import agent as agentmod

agent = AgentInfo(
    name="my-agent",
    description="Custom agent",
    mode="subagent",
    permission=[
        {"permission": "read", "pattern": "*", "action": "allow"},
        {"permission": "*", "pattern": "*", "action": "deny"},
    ]
)
```

---

## ProcessorEvent Stream Handling

```python
async for event in process_stream(ctx, stream_input):
    if event.type == "text_delta":
        print(event.data["content"], end="", flush=True)
    
    elif event.type == "tool_start":
        print(f"\n🔧 Calling {event.data['tool']}")
    
    elif event.type == "tool_running":
        print(f"  Input: {event.data['input']}")
    
    elif event.type == "tool_done":
        if event.data["status"] == "error":
            print(f"  ❌ Error: {event.data['output']}")
        else:
            print(f"  ✓ Result: {event.data['output'][:100]}")
    
    elif event.type == "error":
        print(f"❌ Error: {event.data['message']}")
    
    elif event.type == "finish":
        result = event.data["result"]  # "continue" or "stop"
        parts = event.data["parts"]
        print(f"Iteration complete: {result}")
```

---

## Key Files & Locations

| File | Purpose |
|------|---------|
| `mycode/agent/agent.py` | Agent definitions, loading, discovery |
| `mycode/session/processor.py` | Core agentic loop, tool execution |
| `mycode/tool/base.py` | Tool base classes, ToolResult, capabilities |
| `mycode/tool/registry.py` | Tool registration, visibility control |
| `mycode/session/loop_guard.py` | Three-layer protection, caching, step state |
| `mycode/session/prompt.py` | Orchestration, session management, reminders |
| `mycode/tool/task.py` | Sub-agent spawning via `task` tool |
| `mycode/permission/` | Permission evaluation, rules |

---

## Permission Action Flow

```
Tool call requested
  ↓
Is permission_manager present?
  ├─ No → Tool proceeds
  └─ Yes → permission_manager.ask(tool, ruleset)
    ├─ action == "allow" → Tool proceeds
    ├─ action == "deny" → ToolError("Denied"), doom_count++
    └─ action == "ask"
      ├─ Interactive? (user present) → Ask user
      │  ├─ User says yes → Tool proceeds
      │  └─ User says no → ToolError("Rejected")
      └─ Non-interactive? (sub-agent) → ToolError("Requires permission")
```

---

## Loop Guard State & Checkpoint

```python
guard.begin_step(iteration) → StepState
  # StepState: iteration, status, tool_calls, text_produced, text_length, etc.

guard.record_tool_call(tool_name, input, output, is_error)
  # Adds to history (sliding window)
  # Checks patterns
  # Caches successful read-only results
  # Invalidates cache on mutations

guard.complete_step(step, text_length)
  # Mark complete, update text streak

checkpoint = guard.checkpoint
  # Serializable: steps[], empty_text_streak, total_text_length, cache_stats
```

---

## Error Types

| Error | Cause | Handling |
|-------|-------|----------|
| `ToolNotFoundError` | Tool not registered | Doom detection (doom_count++) |
| `ToolValidateError` | Pydantic validation failed | Tool error, doom detection |
| `ToolRuntimeError` | Tool execution raised exception | Tool error, retry eligible |
| `RejectedError` | User denied permission (ask→no) | Tool blocked |
| `DeniedError` | Agent ruleset denies access | Tool blocked |

---

## Performance Tips

1. **Use `is_read_only()` + `is_concurrency_safe()`** to enable parallelization
2. **Cacheable tools are deduped** automatically by loop guard
3. **Pattern detection** prevents wasteful repeats automatically
4. **Task tool** for heavy work → offload to sub-agent (parallel independent loops)
5. **System reminders** incremental strategy avoids repetition
6. **Tool hiding** via `registry.hide()` reduces LLM context

---

## Debugging

Enable debug mode in `prompt()`:

```python
async for event in prompt(prompt_input, bus, debug=True):
    ...
```

Writes JSON files to `.mycode/debug/{session_id}/iter{N}_{phase}.json` with:
- Full messages
- Token counts
- Tool calls with cached flags
- System/tools definitions

---

## Token Accounting

Per iteration:
```python
tokens_snap_input = assistant_msg.tokens_input
# ... LLM call ...
iter_tokens = assistant_msg.tokens_input - tokens_snap_input

# Includes:
# - input_tokens: actual from API
# - output_tokens: actual from API
# - cache_read_tokens: from prefix cache
# - cache_write_tokens: written to cache
# - reasoning_tokens: extended thinking (if model supports)

prev_iter_usage = {
    "input_tokens": iter_tokens,
    "output_tokens": ...,
    "cache_read_tokens": ...,
    "cache_write_tokens": ...,
    "reasoning_tokens": ...,
    "total_cost": ...,
}
```

---

## System Reminders (Incremental Strategy)

**Skills**: First call = full list. Additions only = incremental. No change = omit.
**Date**: First call = full date. Changed = update. No change = omit.
**Memory**: Always included if memories found via similarity search.

Reduces token waste on repeated calls.
