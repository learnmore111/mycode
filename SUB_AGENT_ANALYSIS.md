# Sub-Agent System Implementation Analysis

## Executive Summary

This Python AI coding agent framework implements a sophisticated sub-agent system where the parent agent can spawn child agents (sub-agents) to handle complex multi-step tasks independently. After thorough analysis of the codebase, I've identified the system architecture, communication patterns, and several potential bugs and design issues.

## 1. Agent Definition and Configuration

### 1.1 Agent Types (`mycode/agent/agent.py`)

**File**: `mycode/agent/agent.py`

Agents are defined using the `AgentInfo` dataclass (lines 30-48):

```python
@dataclass
class AgentInfo:
    name: str
    description: str = ""
    mode: Literal["subagent", "primary", "all"] = "primary"
    native: bool = False
    hidden: bool = False
    prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    color: str | None = None
    model: dict[str, str] | None = None  # {"providerID": ..., "modelID": ...}
    variant: str | None = None
    permission: list[dict[str, Any]] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    steps: int | None = None
```

**Agent Modes**:
- `"primary"` - Can be selected as the main agent for a session (e.g., "build", "plan")
- `"subagent"` - Only spawnable by other agents via the task tool (e.g., "general", "explore")
- `"all"` - Available in both contexts

**Built-in Agents** (lines 65-146):
1. **"build"** (primary): Default agent for executing tools with permission checks
2. **"plan"** (primary): Plan mode that disallows edit tools
3. **"general"** (subagent): General-purpose subagent for multi-step reasoning
4. **"explore"** (subagent): Fast code exploration with limited tools (grep, glob, read only)
5. **"compaction"** (hidden): Internal agent for context summarization
6. **"title"** (hidden): Generates session titles
7. **"summary"** (hidden): Generates session summaries

**Configuration Loading** (lines 153-202):
- Agents are cached globally in `_cached_agents` (line 149)
- Thread-safe caching via `_agents_lock` (line 150)
- Custom agents can be loaded from config and merged with built-ins

### 1.2 Permission System

Each agent has a `permission` field containing rules like:
```python
[
    {"permission": "*", "pattern": "*", "action": "allow"},
    {"permission": "read", "pattern": "*.env", "action": "ask"},
]
```

Sub-agents like "explore" have restrictive permissions:
- Allows only: grep, glob, list, bash, read, webfetch, websearch, codesearch
- Denies everything else

---

## 2. Sub-Agent Spawning via Task Tool

### 2.1 Task Tool Implementation

**File**: `mycode/tool/task.py`

The `TaskTool` (lines 48-182) is the primary mechanism for spawning sub-agents.

**Parameters**:
```python
class TaskParams(BaseModel):
    description: str  # Task description for the sub-agent
    agent: str        # Agent name (default: "general")
```

**Spawning Process** (lines 62-179):

```python
async def call(self, params: TaskParams, ctx: ToolContext) -> ToolResult:
    agent_name = params.agent
    
    # 1. Resolve agent
    agent = await agentmod.get(agent_name)
    if not agent:
        return ToolError(f"Agent '{agent_name}' not found", ...)
    
    # 2. Get default model
    provider_id, model_id = await providermod.default_model()
    model = await providermod.get_model(provider_id, model_id)
    
    # 3. Build system prompt
    system = build_system(agent_prompt=agent.prompt)
    
    # 4. Initialize messages with task description
    messages: list[dict[str, Any]] = [{"role": "user", "content": description}]
    
    # 5. Get available tools (excluding task, todo, question, batch)
    tools = [t for t in tool_registry.to_llm_tools() 
             if t["function"]["name"] not in _EXCLUDED_TOOLS]
    
    # 6. Run agentic loop (up to MAX_TURNS=8)
    for turn in range(MAX_TURNS):
        # ... stream LLM response
        # ... execute tool calls
        # ... check abort signal
        # ... build next iteration messages
```

**Key Characteristics**:

1. **Independent Context**: Each sub-agent gets:
   - Fresh system prompt (based on selected agent)
   - Empty message history (only task description)
   - Restricted tool set (no nested task/todo/question/batch)
   - MAX_TURNS=8 iteration limit

2. **No Session Creation**: Sub-agents do NOT create new database sessions. They run entirely in-memory within the parent's tool execution context.

3. **Tool Execution** (lines 156-172):
   ```python
   tool_impl = tool_registry.get(tc.tool_name)
   if tool_impl:
       result = await tool_impl.execute(tool_args, ctx)  # ← ISSUE!
       tool_output = result.output
   ```
   **CRITICAL BUG**: The parent's `ToolContext` is passed directly to sub-agent tool calls. This means:
   - The `session_id` still points to the PARENT session
   - The `message_id` is the parent's message ID
   - File operations will be attributed to the parent session
   - The `abort` signal is shared (which is good for cancellation, but creates shared state)

4. **Result Truncation**: Output is limited to 50,000 characters via `ToolResultBuilder`

---

## 3. Communication: Parent ↔ Child

### 3.1 Context Flow (ToolContext)

**File**: `mycode/tool/base.py` (lines 29-42)

```python
@dataclass
class ToolContext:
    session_id: str           # ← Inherited from parent
    message_id: str           # ← Inherited from parent
    agent: str                # ← Set to sub-agent name
    abort: Any = None         # ← Shared asyncio.Event
    call_id: str = ""
    messages: list[Any] = field(default_factory=list)  # ← UNUSED!
```

**Issues with Context Passing**:

1. **Session ID Propagation** (Line 161 in task.py):
   - Sub-agent tool calls execute with parent's `session_id`
   - All file modifications are attributed to parent session
   - Creates attribution confusion in logs/history

2. **Message ID Propagation**:
   - All tool results in sub-agent are tagged with parent's `message_id`
   - No ability to distinguish sub-agent message parts

3. **Unused Messages Field**:
   - `ToolContext.messages` is defined but never populated or used
   - Suggests incomplete design for passing conversation history to sub-agents
   - No way for sub-agent to access parent's previous messages

4. **Abort Signal Sharing**:
   - Good: Allows parent to cancel sub-agent execution
   - Potential issue: If sub-agent creates multiple tools, they all share one abort signal
   - Fine for now since tools can't spawn sub-agents, but could be problematic for nested agents

### 3.2 Message Construction

**Sub-agent receives** (line 77 in task.py):
```python
messages: list[dict[str, Any]] = [{"role": "user", "content": description}]
```

This is a completely fresh message history! The sub-agent has:
- ✅ No knowledge of parent's previous context
- ✅ Independent reasoning
- ❌ No parent-child conversation continuity
- ❌ No shared decision history

**Results are returned as** (lines 174-179 in task.py):
```python
return ToolOk(
    output or "No output from sub-agent.",
    title=f"Task: {description[:60]}",
    metadata={"agent": agent_name, "tool_calls": total_tool_calls, "turns": turn + 1}
)
```

Output is sent back to parent as a single tool result message. The parent incorporates this into its next iteration.

### 3.3 Tool Execution Path

When a sub-agent tool is called (line 161 in task.py):

```
Sub-agent Tool Call (task.py:161)
    ↓
tool_impl.execute(tool_args, ctx)  ← ctx from parent!
    ↓
Tool's execute() (e.g., read.py, bash.py)
    ↓
Tool may check session_id, use project context, etc.
```

**Files affected**: Any tool that checks `ctx.session_id` or uses `current_or_none()` project context will see the parent's context.

---

## 4. Session Management for Sub-Agents

### 4.1 No Sub-Session Creation

**Finding**: Sub-agents do NOT get their own database sessions.

Evidence from `mycode/session/session.py`:
- `create()` function (lines 85-100) would create a new SessionInfo
- Sub-agents never call this
- No `parent_id` tracking for sub-agent sessions

**Design Decision**: Sub-agents are ephemeral, in-memory only. This means:
- ✅ No database overhead
- ✅ No session history for sub-agents
- ❌ Sub-agent work is not persisted
- ❌ Sub-agent results only exist in parent's message history

### 4.2 Project Context Isolation

**File**: `mycode/project/instance.py`

Uses Python's `contextvars.ContextVar` for per-async-task context:

```python
_instance_var: ContextVar[InstanceContext | None] = ContextVar("instance", default=None)
```

**Issue**: When a sub-agent runs, it inherits the parent's `InstanceContext`:
- Same working directory
- Same project ID
- Same worktree

This is CORRECT behavior for a sub-agent, but it means:
- Sub-agents cannot switch projects
- Sub-agents cannot change working directories
- Sub-agents are bound to parent's workspace

### 4.3 Loop Guard and Iteration Limits

**File**: `mycode/session/loop_guard.py`

Each sub-agent has its OWN loop guard instance:

```python
# Each sub-agent instantiates its own (not in task.py, implicit via agentic loop)
guard = LoopGuard(config=guard_config)
```

Wait... checking task.py lines 62-179: **Sub-agents don't use LoopGuard at all!**

```python
# LoopGuard is only used in the main agentic loop (prompt.py)
# Task tool runs its own custom loop with MAX_TURNS=8 hardcoded
for turn in range(MAX_TURNS):  # Line 84 in task.py
    if _is_aborted(ctx):
        break
    # ... LLM call and tool execution ...
```

**Sub-Agent Loop** (task.py lines 84-179):
- Max 8 iterations (hardcoded, not configurable)
- Checks abort signal between turns
- No pattern detection for doom loops
- No result caching (unlike parent's LoopGuard)
- Simpler implementation, less sophisticated protection

---

## 5. Issues, Bugs, and Design Problems

### 5.1 CRITICAL ISSUES

#### Issue #1: Context Leakage - File Attribution Error
**Location**: `mycode/tool/task.py:161`

**Problem**:
```python
result = await tool_impl.execute(tool_args, ctx)  # ctx is PARENT's context!
```

When a sub-agent calls tools, the parent's `session_id` and `message_id` are used:
- File writes are attributed to parent session
- Tool results in message history show parent's message ID
- Impossible to audit which tool calls came from sub-agent vs parent

**Severity**: HIGH - Breaks attribution and audit trails

**Fix**:
```python
# Create a modified context for sub-agent tools
sub_ctx = ToolContext(
    session_id=ctx.session_id,  # Keep parent's session for now
    message_id=f"{ctx.message_id}:subagent",  # Prefix to mark as sub-agent
    agent=agent_name,  # This is already done
    abort=ctx.abort,
    call_id=tc.tool_call_id,
    # Don't pass parent's messages
)
result = await tool_impl.execute(tool_args, sub_ctx)
```

#### Issue #2: Missing Context in ToolContext
**Location**: `mycode/tool/base.py:29-42`

**Problem**:
```python
@dataclass
class ToolContext:
    # ...
    messages: list[Any] = field(default_factory=list)  # ← Never populated!
```

The `messages` field suggests tools should receive conversation history, but:
- It's always empty (default_factory=list)
- Never populated in task.py
- Never populated in processor.py
- Unused by any tool

**Severity**: MEDIUM - Dead code, suggests incomplete feature

**Impact**: Tools can't access parent/sub-agent conversation context

#### Issue #3: No Error Propagation from Sub-Agents
**Location**: `mycode/tool/task.py:163-164`

**Problem**:
```python
except Exception as e:
    tool_output = f"Error: {e}"  # ← Swallows exception!
```

If a sub-agent tool crashes, the error is converted to a string and sent to LLM. The parent has no way to know:
- Whether the sub-agent succeeded or failed
- The root cause of failure
- The severity of the error

**Severity**: MEDIUM - Degrades error reporting

**Fix**: Return structured error in ToolResult with is_error flag, then check it in task.py

#### Issue #4: No Result Caching in Sub-Agents
**Location**: `mycode/tool/task.py` (entire tool)

**Problem**:
Sub-agents run their own LLM loop but:
- Don't use LoopGuard's `ToolResultCache`
- Don't deduplicate repeated tool calls
- Duplicate read operations within sub-agent would call tool multiple times

Example: If a sub-agent reads the same file twice, it will call the read tool twice.

**Severity**: LOW - Performance issue, not correctness

**Mitigation**: Could use LoopGuard inside task.py

#### Issue #5: Hardcoded MAX_TURNS = 8 for Sub-Agents
**Location**: `mycode/tool/task.py:24`

**Problem**:
```python
MAX_TURNS = 8  # Hardcoded!
```

Sub-agents are limited to 8 iterations regardless of agent configuration.

Parent agents can have configurable `steps` (from AgentInfo.steps).

**Severity**: LOW - Limits sub-agent capability, but prevents runaway loops

**Better**: Could use agent.steps if available:
```python
max_turns = agent.steps or 8
for turn in range(max_turns):
```

#### Issue #6: TOCTOU in Sub-Agent Context 
**Location**: `mycode/tool/task.py:62-75`

**Problem**:
```python
agent = await agentmod.get(agent_name)
if not agent:
    return ToolError(f"Agent '{agent_name}' not found", ...)

# ... later ...
model = await providermod.get_model(provider_id, model_id)
if not model:
    return ToolError(f"Model error: {e}", ...)
```

These are awaited calls. Between the check and use, configuration could change (though unlikely).

**Severity**: VERY LOW - Unlikely in practice, but poor defensive programming

#### Issue #7: Abort Signal Check Timing
**Location**: `mycode/tool/task.py:86, 104, 148`

**Problem**:
```python
if _is_aborted(ctx):  # Line 86 - before streaming
    builder.add("\n\n(Sub-agent aborted by user)")
    break

# ... stream gets data ...

if _is_aborted(ctx):  # Line 104 - during streaming
    # abort mid-stream
    return ToolOk(builder.build() or "Sub-agent aborted.", ...)

# ... process tool calls ...

if _is_aborted(ctx):  # Line 148 - before tool exec
    messages.append({"role": "tool", "tool_call_id": tc.tool_call_id, "content": "Aborted by user"})
    continue
```

Issue: When abort happens during a tool call execution, there's no mechanism to cancel the actual tool (e.g., if bash is running a long command).

The tool_impl.execute() call (line 161) cannot be interrupted mid-execution.

**Severity**: MEDIUM - Abort may not actually stop work

**Fix**: Need cancellation tokens in tool execution, not just pre-checks

---

### 5.2 DESIGN ISSUES

#### Design Issue #1: Sub-Agent Tool Set Inconsistency
**Location**: `mycode/tool/task.py:26-27, 78`

```python
_EXCLUDED_TOOLS = frozenset({"task", "todo", "question", "batch"})

tools = [t for t in tool_registry.to_llm_tools() 
         if t["function"]["name"] not in _EXCLUDED_TOOLS]
```

Sub-agents get access to most tools, but:
- Can't spawn their own sub-agents (no nested task)
- Can't ask questions (no question tool)
- Can't create todos (no todo tool)
- Can't do batch operations (no batch tool)

This makes sense for preventing infinite nesting, but:
- ❌ Can't ask user for clarification
- ❌ Can't manage sub-tasks
- ❌ Can't do parallel operations
- ✅ Prevents infinite nesting

**Better Design**: Allow nested sub-agents up to depth N (e.g., depth 2), with configurable limit.

#### Design Issue #2: No Sub-Agent Progress Tracking
**Location**: `mycode/tool/task.py:174-179`

Sub-agent only returns final output:
```python
return ToolOk(
    output or "No output from sub-agent.",
    title=f"Task: {description[:60]}",
    metadata={"agent": agent_name, "tool_calls": total_tool_calls, "turns": turn + 1}
)
```

Parent never sees:
- Individual tool outputs from sub-agent
- Which tools failed/succeeded
- Sub-agent's reasoning steps
- Intermediate results

**Better Design**: Could return structured metadata with per-tool results:
```python
metadata={
    "agent": agent_name,
    "tool_calls": total_tool_calls,
    "turns": turn + 1,
    "tools": [
        {"name": "read", "status": "success", "output_length": 1234},
        {"name": "bash", "status": "error", "error": "..."}
    ]
}
```

#### Design Issue #3: No Sub-Agent Session Visibility
**Location**: `mycode/session/session.py`

Sub-agents don't create SessionInfo records, so:
- ❌ No way to view sub-agent's work later
- ❌ No audit trail of sub-agent operations
- ❌ Can't replay sub-agent's steps
- ✅ Simpler, ephemeral design
- ✅ No database overhead

This might be intentional (sub-agents are temporary helpers), but it limits observability.

---

### 5.3 RACE CONDITIONS AND CONCURRENCY ISSUES

#### Race Condition #1: Shared Tool Registry Access
**Location**: `mycode/tool/registry.py` and `mycode/tool/task.py:78`

```python
tools = [t for t in tool_registry.to_llm_tools() 
         if t["function"]["name"] not in _EXCLUDED_TOOLS]
```

The tool registry is global:
```python
_tools: dict[str, ToolInfo] = {}
```

If one request modifies registry while another is spawning a sub-agent, there's a potential race:

```python
# Main loop  |  Sub-agent spawning
register(t1) |  to_llm_tools()
             |  # might see inconsistent state
register(t2) |  get("task")
```

**Severity**: LOW - Unlikely since registry is usually static after startup

**Mitigation**: Registry reads should use a lock:
```python
def visible_tools() -> list[ToolInfo]:
    with _registry_lock:  # ← Add this
        return [t for t in _tools.values() if t.id not in _hidden]
```

#### Race Condition #2: Session Lock Acquisition
**Location**: `mycode/session/prompt.py:61-78`

```python
async def _acquire_session(session_id: str) -> bool:
    async with _locks_mutex:  # Global mutex
        if session_id not in _session_locks:
            _session_locks[session_id] = _aio.Lock()
        lock = _session_locks[session_id]
        if lock.locked():
            return False
        await lock.acquire()
        return True
```

This prevents parallel processing of same session, which is good.

BUT: Sub-agents run WITHIN a parent tool call, which already holds the parent session lock!

```
Parent (session_A) acquires lock
└─ runs tools
   └─ task tool spawns sub-agent
      └─ sub-agent also runs with session_A context
         └─ but never tries to acquire lock (no separate session)
            └─ so this is fine (not a race)
```

Actually, no race here since sub-agents don't acquire their own session lock. They reuse parent's.

---

### 5.4 ERROR HANDLING ISSUES

#### Error Handling #1: Agent Not Found
**Location**: `mycode/tool/task.py:66-68`

```python
agent = await agentmod.get(agent_name)
if not agent:
    return ToolError(f"Agent '{agent_name}' not found", title=f"Task ({agent_name})")
```

Good: Returns error to parent.

But: Should this be logged? Should parent agent try a different agent?

#### Error Handling #2: Cascading Failures
**Location**: `mycode/tool/task.py:72-74`

```python
try:
    provider_id, model_id = await providermod.default_model()
    model = await providermod.get_model(provider_id, model_id)
except Exception as e:
    return ToolError(f"Model error: {e}", title=f"Task ({agent_name})")
```

If model is unavailable, sub-agent fails completely. Parent might want to:
- Retry with different model
- Degrade gracefully
- But instead, task tool fails immediately

#### Error Handling #3: Tool Execution Inside Sub-Agent
**Location**: `mycode/tool/task.py:161-164`

```python
try:
    result = await tool_impl.execute(tool_args, ctx)
    tool_output = result.output
except Exception as e:
    tool_output = f"Error: {e}"
```

This catches ALL exceptions and converts to string. If a tool crashes with a stack trace, it's now just text. The LLM sees it but there's no structured error info.

Better:
```python
result = await tool_impl.execute(tool_args, ctx)
tool_output = result.output if not result.is_error else f"Error: {result.output}"
# This preserves structured error info
```

---

### 5.5 LOGGING AND OBSERVABILITY

#### Observability #1: Sub-Agent Operations Not Logged with Context
**Location**: `mycode/tool/task.py` (entire file)

Sub-agent operations (lines 84-172) don't log:
- Which sub-agent is running
- Parent-child relationship
- Tool call details
- Iteration counts

Should add logs like:
```python
logger.info("sub_agent_start", agent=agent_name, description=description[:60], parent_session=ctx.session_id)
logger.info("sub_agent_turn", turn=turn, text_length=len(assistant_text), tools_called=len(pending_tool_calls))
logger.info("sub_agent_complete", turns=turn+1, total_tools=total_tool_calls, status="success"/"aborted")
```

#### Observability #2: No Metrics Export
Sub-agent execution has no metrics (CPU, memory, API calls, latency) exported.

---

## 6. Summary of Findings

### Findings Table

| Category | Issue | Severity | Location | Type |
|----------|-------|----------|----------|------|
| Context | Session ID leaked to sub-agent tools | HIGH | task.py:161 | Bug |
| Context | Unused messages field in ToolContext | MEDIUM | base.py:37 | Design |
| Error | No error propagation from sub-agent tools | MEDIUM | task.py:164 | Bug |
| Performance | No result caching in sub-agents | LOW | task.py | Design |
| Capability | Hardcoded MAX_TURNS=8 | LOW | task.py:24 | Design |
| Abort | Can't cancel running tools in sub-agent | MEDIUM | task.py:161 | Design |
| Tool Access | Tool registry concurrent access potential | LOW | registry.py | Race |
| Nested Agents | Can't spawn sub-agents from sub-agents | MEDIUM | task.py:26 | Design |
| Visibility | Sub-agent work not persisted in DB | MEDIUM | task.py | Design |
| Attribution | Sub-agent work attributed to parent | MEDIUM | task.py | Design |

### Key Strengths

✅ **Good Isolation**: Sub-agents have independent LLM loops and don't interfere
✅ **Clean API**: TaskParams with description and agent name
✅ **Permission Model**: Sub-agents have restricted permissions (e.g., explore can't edit)
✅ **Abort Support**: Parent can cancel sub-agent via abort signal
✅ **Efficient**: No database overhead for ephemeral sub-agents

### Key Weaknesses

❌ **Context Leakage**: Sub-agent tools use parent's session/message IDs
❌ **No Visibility**: Sub-agent work not visible in session history
❌ **Limited Tools**: Can't spawn nested sub-agents or ask questions
❌ **Poor Error Handling**: Exceptions swallowed, converted to strings
❌ **No Metrics**: Sub-agent performance not tracked

---

## 7. Recommendations

### Priority 1: Fix Context Leakage
```python
# In task.py line 156-172
sub_ctx = ToolContext(
    session_id=ctx.session_id,
    message_id=ctx.message_id,  # or f"{ctx.message_id}:subagent"?
    agent=agent_name,
    abort=ctx.abort,
    call_id=tc.tool_call_id,
)
result = await tool_impl.execute(tool_args, sub_ctx)
```

### Priority 2: Fix Error Reporting
```python
# In task.py line 161-164
result = await tool_impl.execute(tool_args, ctx)
tool_output = result.output
if result.is_error:
    # Better: preserve error structure
    tool_output = f"Error: {result.title}: {result.output}"
```

### Priority 3: Add Logging
```python
logger.info("sub_agent_spawned", agent=agent_name, parent_session=ctx.session_id)
logger.info("sub_agent_completed", agent=agent_name, turns=turn+1, status="success")
```

### Priority 4: Optional - Allow Nested Sub-Agents (with depth limit)
```python
# Configuration
MAX_NESTING_DEPTH = 2

# In task.py
depth = ctx.call_id.count(":subagent")
if depth >= MAX_NESTING_DEPTH:
    return ToolError("Maximum nesting depth reached")
```

### Priority 5: Optional - Persistent Sub-Agent Sessions
```python
# In task.py
sub_session = session.create(parent_id=ctx.session_id)
# Now sub-agent operations are persisted
```

---

## 8. Code Quality Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| Clarity | ⭐⭐⭐⭐ | Code is well-structured and readable |
| Robustness | ⭐⭐⭐ | Some error handling gaps |
| Concurrency | ⭐⭐⭐ | Mostly safe, one potential race condition |
| Testability | ⭐⭐⭐ | Limited test coverage for sub-agents |
| Documentation | ⭐⭐⭐ | Some docstrings, but overall adequate |
| Performance | ⭐⭐⭐⭐ | Efficient use of async, good parallelization in parent |

