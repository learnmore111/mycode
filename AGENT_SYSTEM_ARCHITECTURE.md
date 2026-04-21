# Agent System Architecture - Comprehensive Analysis

## Overview

This is a sophisticated agentic loop system supporting multiple AI model providers with:
- **Multi-agent architecture** (primary agents, sub-agents, hidden utility agents)
- **Permission-based tool access control** 
- **Three-layer loop guard** (hard limit, pattern detection, near-limit intelligence)
- **Tool result caching** for read-only operations
- **Read/write separation** for parallel vs sequential tool execution
- **Structured tool system** with Pydantic validation

---

## 1. AGENT CLASS STRUCTURE (`mycode/agent/agent.py`)

### AgentInfo Dataclass

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

### Key Fields:

- **`mode`**: Determines agent visibility and usage pattern
  - `"primary"`: User-selectable agent for main conversation (build, plan)
  - `"subagent"`: Only callable via `task` tool (general, explore)
  - `"all"`: Custom agents can be used in any context
  
- **`permission`**: List of permission rules controlling tool access
  - Each rule: `{"permission": "tool_id", "pattern": "*", "action": "allow|deny|ask"}`
  - Evaluated per tool call in the processor loop
  
- **`steps`**: Max iterations for this agent (overrides loop guard default of 50)

- **`native`**: Built-in agent (true) vs custom (false)

- **`hidden`**: Utility agents not shown in UI (compaction, title, summary)

---

## 2. DEFAULT AGENTS (`_build_agents()`)

### Primary Agents (User-selectable)

#### **build** Agent
- **Mode**: `primary`, native
- **Purpose**: Default agent, executes tools with configured permissions
- **Special Permissions**:
  - Allows `question` and `plan_enter` tools
  - Enables interactive user Q&A
  - Can enter plan mode
  
#### **plan** Agent
- **Mode**: `primary`, native
- **Purpose**: Read-only mode, disallows all edit tools
- **Special Permissions**:
  - Denies all `edit` tools
  - Allows `question` and `plan_exit` tools
  - For safe exploration without mutations

### Sub-agents (Only invoked via `task` tool)

#### **general** Agent
- **Mode**: `subagent`, native
- **Purpose**: General-purpose multi-step reasoning in parallel
- **Permissions**: Most tools allowed except `todowrite`
- **Used for**: Complex research, parallel task execution
- **Features**: Full loop guard caching + pattern detection

#### **explore** Agent
- **Mode**: `subagent`, native
- **Purpose**: Fast codebase exploration and search
- **Permissions**: ONLY read tools
  - `grep`, `glob`, `list`, `bash`, `read`, `webfetch`, `websearch`, `codesearch`
- **Default deny**: All write/mutation tools
- **Used for**: File searches, code analysis, Q&A

### Utility Agents (Hidden, internal use)

#### **compaction** Agent
- **Mode**: `primary`, native, hidden
- **Purpose**: Message summarization for context overflow
- **Permissions**: None (deny everything)

#### **title** Agent
- **Mode**: `primary`, native, hidden
- **Purpose**: Generate session titles
- **Permissions**: None

#### **summary** Agent
- **Mode**: `primary`, native, hidden
- **Purpose**: Generate session summaries
- **Permissions**: None

---

## 3. AGENT LOADING & CUSTOMIZATION

### Loading Flow

```
_load_agents() 
  → _build_agents() [built-in defaults]
  → config.agent overrides [from config.yaml]
  → returns merged dict[name, AgentInfo]
```

### Configuration Override

Agents can be customized in `config.yaml`:

```yaml
agent:
  my-custom-agent:
    model: "openai/gpt-4-turbo"
    prompt: "Custom system prompt here..."
    temperature: 0.7
    mode: "subagent"
    permission:
      - permission: "read"
        pattern: "*"
        action: "allow"
    steps: 100  # Custom iteration limit
```

### Agent Discovery

```python
async def list_agents() -> list[AgentInfo]
  # Returns user-selectable agents (non-hidden, non-subagent-only)
  # Sorted with default first

async def get(name: str) -> AgentInfo | None
  # Thread-safe retrieval, returns a copy to prevent cache mutation

async def default_agent() -> str
  # Returns "build" or config.default_agent if valid
```

---

## 4. PROCESSOR LOOP FLOW (`mycode/session/processor.py`)

### Architecture: Streaming Event Generator

```python
async def process_stream(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> AsyncGenerator[ProcessorEvent, None]
```

Yields events in real-time as the LLM streams and tools execute.

### ProcessorContext

```python
@dataclass
class ProcessorContext:
    session_id: str
    model: Model
    assistant_message: AssistantMessage
    bus: Bus
    toolcalls: dict[str, ToolPart]       # tool_call_id → ToolPart
    parts: list[Part]                    # Accumulated text + tool parts
    should_break: bool
    doom_count: int
    permission_manager: PermissionManager | None
    agent_permission: list[Rule]         # Agent's permission ruleset
    loop_guard: LoopGuard | None         # For pattern detection + caching
```

### Loop Iteration Phases

#### **Phase 0: LLM Streaming**
```
for event in llmmod.stream(stream_input):
  TextDelta         → accumulates in current_text
  ToolCallPartial   → creates ToolPart, status="pending"
  ToolCallArgsPartial → parses JSON args incrementally
  ToolCallDelta     → finalizes args in tp.state["input"]
  FinishEvent       → updates token counts, costs
  ErrorEvent        → sets should_break, yields error
```

#### **Phase 1: Pre-flight Checks**
For each pending tool call:

1. **Tool Lookup**: `tool_registry.get_or_raise(tp.tool)`
   - Raises `ToolNotFoundError` if not found

2. **Permission Check** (if `permission_manager` present):
   ```python
   await permission_manager.ask(
       permission=tp.tool,
       patterns=["*"],
       ruleset=ctx.agent_permission,
       metadata={"tool": tp.tool, "input": tp.state.get("input")}
   )
   ```
   - Raises `RejectedError` or `DeniedError` on failure
   - Sets tool status="error"

3. **Doom Loop Detection** (legacy):
   ```python
   recent_tool_parts = [p for p in ctx.parts if isinstance(p, ToolPart) and p.tool == tp.tool]
   if len(recent_tool_parts) >= DOOM_LOOP_THRESHOLD:
       # Check if last 3 calls have identical input → error
   ```

4. **Cache Check** (read-only tools only):
   ```python
   cached = cache.get(tp.tool, tp.state.get("input", {}))
   if cached is not None:
       # Skip execution, use cached result
   ```

#### **Phase 2: Tool Execution with Read/Write Separation**

Separates tools into two categories:

**Read-Only Tools** → Run in parallel via `asyncio.gather()`
```python
if hasattr(tool_impl, "is_concurrency_safe") and hasattr(tool_impl, "is_read_only"):
    if tool_impl.is_read_only(tool_input) and tool_impl.is_concurrency_safe(tool_input):
        readonly_tasks.append((tp, tool_impl, tool_ctx))
```

**Mutating Tools** → Run sequentially
```python
elif tp.tool in MUTATING_TOOLS:  # {"edit", "write", "bash"}
    mutating_tasks.append((tp, tool_impl, tool_ctx))
else:
    readonly_tasks.append(...)  # Fallback
```

**Execution with Retry**:
```python
async def _run_tool_with_retry(tp, tool_impl, tool_ctx, ctx):
    for attempt in range(max_retries + 1):
        success, event = await _run_tool(tp, tool_impl, tool_ctx, ctx)
        if success:
            guard.record_tool_call(...)
            return
        if should_retry(tp.tool, error, attempt):
            await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
            continue
```

#### **Phase 3: Result Processing**

For each tool execution:
```python
result = await tool_impl.execute(tp.state.get("input", {}), tool_ctx)

tp.state["output"] = result.output
tp.state["status"] = "completed" if not result.is_error else "error"
tp.state["is_error"] = result.is_error
tp.time_completed = int(time.time() * 1000)
```

#### **Phase 4: Doom Count & Loop Termination**

```python
if has_failure:
    ctx.doom_count += 1
else:
    ctx.doom_count = 0

if ctx.doom_count >= DOOM_LOOP_THRESHOLD:
    yield ProcessorEvent(type="finish", data={"result": "stop"})
    return
```

### ProcessorEvent Types

| Event Type | Data |
|-----------|------|
| `"text_delta"` | `{"content": str}` |
| `"tool_start"` | `{"tool": str, "call_id": str}` |
| `"tool_running"` | `{"tool": str, "call_id": str, "input": dict}` |
| `"tool_done"` | `{"tool": str, "status": str, "output": str, "input": dict}` |
| `"error"` | `{"message": str}` |
| `"finish"` | `{"result": "continue"\|"stop", "parts": list[Part]}` |

---

## 5. SESSION PROMPT ORCHESTRATION (`mycode/session/prompt.py`)

### Main Entry Point

```python
async def prompt(
    prompt_input: PromptInput,
    bus: Bus,
    *,
    history: list[dict[str, Any]] | None = None,
    debug: bool = False,
    permission_manager: PermissionManager | None = None,
) -> AsyncGenerator[PromptEvent, None]
```

### Flow

```
1. acquire_session_lock()
2. resolve_model() and resolve_agent()
3. build_system_prompt()
4. initialize_loop_guard()
5. for iteration in range(max_iterations):
     - loop_guard.check()          # Three-layer guard
     - snapshot_before_llm()       # Token counts
     - async for event in process_stream():
         - yield PromptEvent
     - guard.complete_step()
     - if result == "continue":
         - build_tool_results_messages()
         - continue
     - else:
         - break
6. persist_turn()
7. release_session_lock()
```

### System Reminder Strategy

Incremental updates for skills, memory, and date:

**Skills**:
- First call or changes: full list
- Only additions: incremental "New skills available"
- No change: omit

**Date**:
- First call: full date
- Changed: "Date has changed..."
- No change: omit

**Memory**:
- Always included if relevant memories found (via similarity search)

---

## 6. TOOL CAPABILITY SYSTEM (`mycode/tool/base.py`)

### ToolInfo Base Class

```python
class ToolInfo(ABC):
    id: str = ""
    description: str = ""
    
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]
    
    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult
    
    # Capability declarations
    def is_read_only(self, args: dict[str, Any] | None = None) -> bool
    def is_destructive(self, args: dict[str, Any] | None = None) -> bool
    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool
    def is_enabled(self) -> bool
```

### Capability Meanings

| Capability | Meaning | Use Case |
|------------|---------|----------|
| `is_read_only()` | Returns True if only reads data, no side effects | Plan mode auto-allow; parallel execution; cache eligible |
| `is_destructive()` | Returns True if irreversible (delete, overwrite, send) | May require extra confirmation |
| `is_concurrency_safe()` | Returns True if safe to run in parallel | Read-only tools can run together |
| `is_enabled()` | Returns True if tool is currently available | Feature flags, conditional tools |

### CallableTool[Params] — Type-Safe Tool Base

```python
class MyTool(CallableTool[MyParams]):
    id = "my_tool"
    description = "..."
    
    def is_read_only(self, args=None) -> bool:
        return True  # or: return args.get("read_only", False)
    
    async def call(self, params: MyParams, ctx: ToolContext) -> ToolResult:
        # params is already validated by Pydantic
        ...
```

**Features**:
- Auto-generates JSON Schema from Pydantic model
- Automatic parameter validation with clear error messages
- Type safety: `call()` receives typed `MyParams` instead of raw dict

### ToolResult & Convenience Constructors

```python
@dataclass
class ToolResult:
    output: str = ""           # Sent to LLM
    message: str = ""          # Also sent to LLM (explanation)
    display: str = ""          # UI-only, not sent to LLM
    is_error: bool = False
    title: str = ""            # UI title
    metadata: dict = {}        # Arbitrary data

class ToolOk(ToolResult):
    # Convenience: is_error=False
    
class ToolError(ToolResult):
    # Convenience: is_error=True
```

### Tool Context

```python
@dataclass
class ToolContext:
    session_id: str
    message_id: str
    agent: str                 # Current agent name
    abort: Any = None          # asyncio.Event for cancellation
    call_id: str = ""
    messages: list[Any] = []   # Conversation history
    
    async def ask_permission(self, *, permission: str, patterns: list[str], 
                            metadata: dict[str, Any] | None = None) -> None
        # For tools that need dynamic permission requests
```

---

## 7. TOOL REGISTRY (`mycode/tool/registry.py`)

### Core Functions

```python
def register(tool: ToolInfo) -> None
    # Add tool to registry, replace if exists

def get(tool_id: str) -> ToolInfo | None
    # Look up tool, return None if not found

def get_or_raise(tool_id: str) -> ToolInfo
    # Look up tool, raise ToolNotFoundError if not found

def visible_tools() -> list[ToolInfo]
    # Non-hidden tools (shown to LLM)

def hide(tool_id: str) -> None
    # Hide tool from LLM (still registered)

def to_llm_tools() -> list[dict[str, Any]]
    # Convert visible tools to litellm format, sorted by name for cache stability

def register_builtins() -> None
    # Register all built-in tools (idempotent)
```

### Built-in Tools

Registered in `register_builtins()`:
- **File I/O**: `read`, `write`, `edit`, `glob`, `listdir`
- **Search**: `grep`
- **Execution**: `bash`
- **Information**: `webfetch`, `websearch`, `question`
- **Task Delegation**: `task`, `todo`
- **Skill Loading**: `skill`
- **Task Tracking**: `task` (create/get/update tasks)
- **Skill Creation**: `create_skill`

### Tool Hiding/Visibility

```python
_hidden: set[str] = set()  # Hidden tool ids

def to_llm_tools() -> list[dict]:
    visible = [t for t in _tools.values() if t.id not in _hidden]
    visible.sort(key=lambda t: t.id)  # Cache stability
    return [t.to_llm_tool() for t in visible]
```

---

## 8. LOOP GUARD SYSTEM (`mycode/session/loop_guard.py`)

### Three-Layer Protection Architecture

```python
def check(self, iteration: int) -> GuardVerdict:
    v1 = self._check_hard_limit(iteration)
    if v1.action in (STOP, FORCE_STOP):
        return v1
    
    v2 = self._check_patterns()
    if v2.action == STOP:
        return v2
    
    v3 = self._check_near_limit(iteration)
    if v3.action == STOP:
        return v3
    
    # Return most restrictive
    return v1 or v3 or CONTINUE
```

### Layer 1: Hard Limit Guard

```python
def _check_hard_limit(self, iteration: int) -> GuardVerdict:
    if iteration >= max_iterations:
        return FORCE_STOP
    if iteration >= warn_at (80%):
        return WARN
    return CONTINUE
```

- **Absolute ceiling**: Never exceeded
- **Non-negotiable**: `GuardAction.FORCE_STOP`

### Layer 2: Pattern Detection Guard

Detects three anomaly patterns:

**A. Repeat Detection**: Same tool + same input ≥ 3 times
```python
if all(r.tool_name == first.tool_name 
       and r.input_hash == first.input_hash 
       for r in recent[-3:]):
    return STOP("Repeat detected")
```

**B. Ping-Pong Detection**: Alternating tools A↔B ≥ 4 times with same inputs
```python
# Tools alternate: A B A B A B A B
# Each tool has single unique input_hash
# Both conditions must hold
```

**C. Stall Detection**: Same tool + input + output ≥ 5 times (no progress)
```python
if all(r.tool_name == first.tool_name 
       and r.input_hash == first.input_hash 
       and r.output_hash == first.output_hash 
       for r in recent[-5:]):
    return STOP("Stall detected")
```

### Layer 3: Near-Limit Intelligence

At 90% of max iterations:

- If N consecutive iterations with no text: STOP
- If last 3 tool calls are errors: STOP
- Otherwise: WARN

### Tool Result Cache

**Content-addressable cache** for deduplication:

```python
class ToolResultCache:
    CACHEABLE_TOOLS = {"read", "glob", "grep", "listdir", "webfetch", "websearch", "skill"}
    
    def get(self, tool_name: str, tool_input: dict) -> str | None:
        if tool_name not in CACHEABLE_TOOLS:
            return None
        key = hash_input(tool_name, tool_input)
        return self._cache.get(key)
    
    def put(self, tool_name: str, tool_input: dict, output: str) -> None:
        # Cache only read-only tools
        
    def invalidate(self) -> None:
        # Clear all on any mutating tool (edit, write, bash)
```

### Step State & Checkpoint

```python
@dataclass
class StepState:
    iteration: int
    status: StepStatus      # PENDING, RUNNING, COMPLETED, FAILED, SKIPPED
    tool_calls: list[dict]
    text_produced: bool
    text_length: int
    error: str | None
    cached_calls: int       # Served from cache
    retry_count: int
```

Enables checkpoint/resume on interruption.

---

## 9. AGENT-TO-AGENT INVOCATION: THE `task` TOOL

The primary mechanism for agent delegation is the **`task` tool**, which spawns a sub-agent:

### TaskTool Implementation (`mycode/tool/task.py`)

```python
class TaskParams(BaseModel):
    description: str  # Task for sub-agent
    agent: str = "general"  # "general" or "explore"

class TaskTool(CallableTool[TaskParams]):
    id = "task"
    # Launches sub-agent with independent context and loop
```

### Sub-Agent Loop Flow

```
for turn in range(MAX_TURNS=8):
    1. loop_guard.check(turn)  # Pattern detection
    2. LLM call with sub-agent's system prompt
    3. Stream events from LLM
    4. For each tool call:
       a. Permission check via agent's ruleset
       b. Cache lookup
       c. Execute tool
       d. Record to guard (cache invalidation on mutate)
    5. Add tool results to messages
```

### Key Features

- **Independent Context**: Sub-agent has own message history, loop guard, cache
- **Permission Enforcement**: Tool calls checked against agent's ruleset
- **Abort Signals**: Parent can abort via `ctx.abort` (asyncio.Event)
- **Cache Sharing**: Sub-agent uses guard.cache for result deduplication
- **Max Turns**: Capped at 8 (not 50) to prevent runaway

### Available Sub-Agents

| Agent | Access | Tools Allowed | Use Case |
|-------|--------|---------------|----------|
| `general` | Via `task` tool | Most (except todowrite) | Multi-step reasoning, parallel work |
| `explore` | Via `task` tool | Read-only only (grep, glob, read, webfetch, websearch) | Fast codebase search, Q&A |

### Example Usage

In main agent's thinking:
```
"This is a complex research task. Let me use the task tool to delegate to the explore agent."

task(description="Search for all TypeScript interfaces that reference User type in src/", 
     agent="explore")
```

---

## 10. PERMISSION SYSTEM

### Permission Ruleset per Agent

Each agent has a permission ruleset (list of rules):

```python
permission: list[dict[str, Any]] = [
    {"permission": "read", "pattern": "*", "action": "allow"},
    {"permission": "write", "pattern": "src/**", "action": "allow"},
    {"permission": "bash", "pattern": "*", "action": "deny"},
    {"permission": "edit", "pattern": "*.json", "action": "ask"},
]
```

### Default Permission (used by `build` agent)

```python
def _default_permission() -> list[dict]:
    return [
        {"permission": "*", "pattern": "*", "action": "allow"},
        {"permission": "doom_loop", "pattern": "*", "action": "ask"},
        {"permission": "external_directory", "pattern": "*", "action": "ask"},
        {"permission": "question", "pattern": "*", "action": "deny"},
        {"permission": "plan_enter", "pattern": "*", "action": "deny"},
        {"permission": "plan_exit", "pattern": "*", "action": "deny"},
        {"permission": "read", "pattern": "*.env", "action": "ask"},
        {"permission": "read", "pattern": "*.env.*", "action": "ask"},
    ]
```

### Evaluation

In `processor.py`:
```python
if ctx.permission_manager:
    await ctx.permission_manager.ask(
        session_id=ctx.session_id,
        permission=tp.tool,
        patterns=["*"],
        ruleset=ctx.agent_permission,
        metadata={"tool": tp.tool, "input": tp.state.get("input", {})}
    )
```

Actions:
- **`"allow"`**: Tool call proceeds
- **`"deny"`**: Tool blocked with error
- **`"ask"`**: User prompted (raises `RejectedError` if no user present, treated as deny for sub-agents)

---

## 11. EXISTING TOOLS & PATTERNS

### Read-Only Tools (Cacheable)

| Tool | is_read_only | is_concurrency_safe | Use |
|------|-------------|-------------------|-----|
| `read` | ✓ | ✓ | Read file contents |
| `glob` | ✓ | ✓ | Find files by pattern |
| `grep` | ✓ | ✓ | Search file contents |
| `listdir` | ✓ | ✓ | List directory |
| `webfetch` | ✓ | ✓ | Fetch & parse web pages |
| `websearch` | ✓ | ✓ | Web search |
| `skill` | ✓ | ✓ | Load skill files |

### Mutating Tools (Sequential, Cache-Invalidating)

| Tool | is_read_only | is_concurrency_safe | Invalidates Cache |
|------|-------------|-------------------|--------------------|
| `bash` | ✗ | ✓ | ✓ |
| `write` | ✗ | ✗ | ✓ |
| `edit` | ✗ | ✗ | ✓ |

### Example: `read` Tool

```python
class ReadTool(CallableTool[ReadParams]):
    id = "read"
    
    def is_read_only(self, args=None) -> bool:
        return True  # No side effects
    
    def is_concurrency_safe(self, args=None) -> bool:
        return True  # Multiple reads OK
    
    async def call(self, params: ReadParams, ctx: ToolContext) -> ToolResult:
        # Read file with line offset support, encoding detection
        ...
```

### Example: `bash` Tool

```python
class BashTool(CallableTool[BashParams]):
    id = "bash"
    
    def is_read_only(self, args=None) -> bool:
        return False  # Cannot determine statically
    
    def is_concurrency_safe(self, args=None) -> bool:
        return True  # Isolated processes
    
    async def call(self, params: BashParams, ctx: ToolContext) -> ToolResult:
        # Execute shell command with timeout, env vars, cwd
        ...
```

---

## 12. COMPLETE AGENTIC LOOP SEQUENCE DIAGRAM

```
┌─────────────────────────────────────────────────────────────────┐
│ SESSION START: prompt()                                         │
│ - Acquire session lock (TOCTOU safe)                            │
│ - Resolve model and agent                                       │
│ - Build system prompt                                           │
│ - Load tools from registry                                      │
│ - Create LoopGuard with cache                                   │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ FOR EACH ITERATION (up to max_iterations):                      │
│                                                                  │
│  1. guard.check(iteration) → GuardVerdict                       │
│     - Layer 1: Hard limit exceeded? FORCE_STOP                  │
│     - Layer 2: Pattern detected? STOP                           │
│     - Layer 3: Near limit + no progress? STOP                   │
│     - Return CONTINUE, WARN, STOP, or FORCE_STOP                │
│                                                                  │
│  2. guard.begin_step(iteration) → StepState                     │
│                                                                  │
│  3. Check context overflow → compact if needed                  │
│                                                                  │
│  4. Build system reminders (skills, date, memory)               │
│                                                                  │
│  5. async for event in process_stream():                        │
│       ┌─────────────────────────────────────────────────────┐  │
│       │ STREAM PHASE:                                       │  │
│       │ - TextDelta → accumulate in current_text            │  │
│       │ - ToolCallPartial → create ToolPart (pending)       │  │
│       │ - ToolCallDelta → parse & validate args             │  │
│       │ - FinishEvent → update tokens/cost                  │  │
│       │ - ErrorEvent → set should_break                     │  │
│       └─────────────────────────────────────────────────────┘  │
│                            ↓                                    │
│       ┌─────────────────────────────────────────────────────┐  │
│       │ PREFLIGHT PHASE (for each tool call):               │  │
│       │ 1. Tool lookup → ToolNotFoundError                  │  │
│       │ 2. Permission check → RejectedError/DeniedError     │  │
│       │ 3. Doom loop check → Error                          │  │
│       │ 4. Cache check → Use cached result or mark exe      │  │
│       └─────────────────────────────────────────────────────┘  │
│                            ↓                                    │
│       ┌─────────────────────────────────────────────────────┐  │
│       │ EXECUTION PHASE:                                    │  │
│       │ - Separate read-only vs mutating tools              │  │
│       │ - Run read-only tools in parallel (asyncio.gather)  │  │
│       │ - Run mutating tools sequentially                   │  │
│       │ - For each: execute() → ToolResult                  │  │
│       │ - Record to guard (cache put/invalidate)            │  │
│       └─────────────────────────────────────────────────────┘  │
│                                                                  │
│  6. guard.complete_step(step, text_length)                      │
│     - Record text production streak                             │
│                                                                  │
│  7. If result == "continue":                                    │
│     - build_tool_results_messages()                             │
│     - Append to messages                                        │
│     - Continue to next iteration                                │
│     Else:                                                       │
│     - Break from loop                                           │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ SESSION END: persist_turn()                                     │
│ - Save user message + text part                                 │
│ - Save assistant message + all parts                            │
│ - Save checkpoint (step state)                                  │
│ - Release session lock                                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 13. KEY DESIGN PATTERNS

### 1. **Event Streaming Architecture**

Generator-based streaming allows real-time rendering:
```python
async for event in process_stream(ctx, stream_input):
    if event.type == "text_delta":
        # Render immediately
    elif event.type == "tool_done":
        # Show tool result
```

### 2. **Capability-Driven Parallelization**

```python
if tool_impl.is_read_only(args) and tool_impl.is_concurrency_safe(args):
    # Run in parallel
else:
    # Run sequentially
```

### 3. **Content-Addressable Caching**

```python
key = hash(tool_name, input)  # Deterministic
cache.get(key)  # Hit/miss
cache.put(key, output)  # Store
cache.invalidate()  # On write
```

### 4. **Permission as Configuration**

Permissions are data-driven, not hard-coded:
```python
agent.permission = [
    {"permission": "read", "pattern": "*.env", "action": "ask"}
]
```

### 5. **Sub-Agents via Tools**

Recursion through tool system:
```
Main Agent 
  → task(description, agent="general")
    → General Sub-Agent (independent loop, own cache)
      → Uses read/write/bash tools
      → Returns result to main agent
```

### 6. **Three-Layer Guard System**

Complementary protection layers prevent different types of failures:
- Hard limit: simple, absolute
- Pattern: detects AI behavior loops
- Near-limit: intelligent early termination

### 7. **Atomic File Writes**

```python
def atomic_write(file_path: str, content: str):
    # Write to temp file in same dir
    # Rename atomically
    # Ensures no half-written files
```

---

## 14. ERROR HIERARCHY

```python
ToolBaseError
  ├── ToolNotFoundError(tool_id)
  │   "Unknown tool: {tool_id}"
  │
  ├── ToolParseError(tool_id, raw, cause)
  │   "Failed to parse arguments: {cause}"
  │
  ├── ToolValidateError(tool_id, errors)
  │   "Validation failed: {field}: {msg}..."
  │
  └── ToolRuntimeError(tool_id, cause)
      "Tool runtime error: {cause}"

Permission Errors:
  ├── RejectedError
  │   Tool call rejected by user (ask → user said no)
  │
  └── DeniedError
      Tool call denied by ruleset (action="deny")
```

---

## 15. CONFIGURATION EXAMPLE

### `config.yaml` with Custom Agent

```yaml
agent:
  data-analyst:
    description: "Specialized for data analysis tasks"
    mode: "subagent"
    model: "openai/gpt-4"
    temperature: 0.5
    steps: 100
    permission:
      - permission: "read"
        pattern: "data/**"
        action: "allow"
      - permission: "bash"
        pattern: "*"
        action: "allow"  # Allow running data scripts
      - permission: "*"
        pattern: "*"
        action: "deny"  # Default deny
```

### Usage

```python
# Invoke via task tool
task(description="Analyze the sales data in data/q4.csv", 
     agent="data-analyst")
```

---

## Summary

| Component | Responsibility |
|-----------|-----------------|
| **Agent** | Configuration (permissions, prompt, model, limits) |
| **Processor** | Core loop: stream LLM → parse tools → preflight → execute |
| **Tool Registry** | Manage available tools, visibility, serialization |
| **Tool Base Classes** | Type-safe execution, capability declarations, results |
| **Loop Guard** | Three-layer protection (hard limit, patterns, near-limit) |
| **Permission Manager** | Evaluate access rules, enforce constraints |
| **Task Tool** | Sub-agent invocation with independent loop |
| **Cache** | Deduplicate read-only tool calls, invalidate on write |
| **Session/Prompt** | Orchestration layer, system reminders, persistence |

The system elegantly separates concerns:
- **Agents** = policy (who can do what)
- **Tools** = capabilities (what can be done)
- **Processor** = execution (how it's done)
- **Guard** = safety (when to stop)
