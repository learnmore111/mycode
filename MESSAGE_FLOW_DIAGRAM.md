# Conversation History Message Flow Diagram

## Complete Flow: Database → OpenAI Format → LLM

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MESSAGE PERSISTENCE LAYER                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Database (SQLite)                                                           │
│  ┌──────────────────────┐                                                   │
│  │ MessageTable         │                                                   │
│  ├──────────────────────┤                                                   │
│  │ id                   │ ← Primary key                                     │
│  │ session_id           │ ← FK to Session                                  │
│  │ role                 │ ← "user" | "assistant"                           │
│  │ parent_id            │ ← For assistant: links to user message           │
│  │ model_id             │ ← For assistant                                  │
│  │ provider_id          │ ← For assistant                                  │
│  │ agent                │ ← For assistant                                  │
│  │ tokens_input/output  │ ← For assistant                                  │
│  │ time_created         │ ← Milliseconds                                   │
│  │ time_completed       │ ← For assistant                                  │
│  └──────────────────────┘                                                   │
│           │                                                                  │
│           └─────────┬──────────────────────────────────────────────┐        │
│                     │ (1:N relationship)                           │        │
│                     ▼                                              │        │
│  ┌──────────────────────────────────────────────────────────┐    │        │
│  │ PartTable                                                │    │        │
│  ├──────────────────────────────────────────────────────────┤    │        │
│  │ id                                                       │    │        │
│  │ message_id (FK)  ────────────────────────────────────────┴────┼───┐    │
│  │ type: "text" | "tool" | "reasoning" | "file"             │   │   │    │
│  │ content  ← For text parts                                │   │   │    │
│  │ tool     ← Tool name for tool parts                      │   │   │    │
│  │ tool_call_id  ← Unique ID for this tool invocation       │   │   │    │
│  │ state    ← TEXT column (JSON string)                     │   │   │    │
│  │ time_created/completed                                  │   │   │    │
│  └──────────────────────────────────────────────────────────┘   │   │    │
│           │                                                      │   │    │
│           │ PartTable.state (JSON):                             │   │    │
│           ├─ For TextPart: null or not used                     │   │    │
│           ├─ For ToolPart:                                      │   │    │
│           │  {                                                  │   │    │
│           │    "status": "pending|running|completed|error",    │   │    │
│           │    "input": {...},        ← Tool arguments         │   │    │
│           │    "output": "...",       ← Tool result            │   │    │
│           │    "is_error": bool,                               │   │    │
│           │    "title": "...",                                 │   │    │
│           │    "message": "...",      ← Optional msg           │   │    │
│           │    "metadata": {...}      ← {"cached": true}       │   │    │
│           │  }                                                  │   │    │
│           └─ Stored as JSON string in TEXT column              │   │    │
│                                                                 │   │    │
└─────────────────────────────────────────────────────────────────┼───┼────┘
                                                                   │   │
                                                                   │   │
┌───────────────────────────────────────────────────────────────┐ │   │
│              APPLICATION CONVERSION LAYER                    │ │   │
├───────────────────────────────────────────────────────────────┤ │   │
│                                                               │ │   │
│  Query Pattern (Python):                                    │ │   │
│  ┌─────────────────────────────────────────────────────────┐ │ │   │
│  │ messages = db.query(MessageTable)                      │ │ │   │
│  │   .filter(MessageTable.session_id == sid)              │ │ │   │
│  │   .order_by(MessageTable.time_created)                 │ │ │   │
│  │   .all()                                                │ │ │   │
│  │                                                          │ │ │   │
│  │ parts = db.query(PartTable)                             │◄┼─┼───┘
│  │   .filter(PartTable.message_id.in_(msg_ids))           │ │ │
│  │   .order_by(PartTable.time_created)                    │ │ │
│  │   .all()                                                │ │ │
│  └─────────────────────────────────────────────────────────┘ │ │
│                          │                                    │ │
│                          ▼                                    │ │
│  Conversion to OpenAI Format:                               │ │
│  ┌─────────────────────────────────────────────────────────┐ │ │
│  │ FOR each MessageTable record:                          │ │ │
│  │                                                          │ │ │
│  │ IF role == "user":                                      │ │ │
│  │   message = {                                           │ │ │
│  │     "role": "user",                                     │ │ │
│  │     "content": "<text from first TextPart>"            │ │ │
│  │   }                                                      │ │ │
│  │                                                          │ │ │
│  │ ELIF role == "assistant":                              │ │ │
│  │   text_parts = [p for p in parts if p.type=="text"]   │ │ │
│  │   tool_parts = [p for p in parts if p.type=="tool"]   │ │ │
│  │                                                          │ │ │
│  │   IF tool_parts:                                        │ │ │
│  │     assistant_msg = {                                  │ │ │
│  │       "role": "assistant",                             │ │ │
│  │       "content": "".join(p.content for p in text_parts)│ │ │
│  │                   or None,                             │ │ │
│  │       "tool_calls": [                                  │ │ │
│  │         {                                              │ │ │
│  │           "id": tp.tool_call_id,                       │ │ │
│  │           "type": "function",                          │ │ │
│  │           "function": {                                │ │ │
│  │             "name": tp.tool,                           │ │ │
│  │             "arguments": json.dumps(                   │ │ │
│  │               tp.state.get("input", {})               │ │ │
│  │             )  ← CRITICAL: JSON STRING not dict        │ │ │
│  │           }                                            │ │ │
│  │         }                                              │ │ │
│  │         for tp in tool_parts                           │ │ │
│  │       ]                                                │ │ │
│  │     }                                                  │ │ │
│  │     messages_list.append(assistant_msg)               │ │ │
│  │                                                          │ │ │
│  │     FOR each tool_part:                                │ │ │
│  │       tool_result = {                                  │ │ │
│  │         "role": "tool",                                │ │ │
│  │         "tool_call_id": tp.tool_call_id,              │ │ │
│  │         "content": tp.state.get("output", "")          │ │ │
│  │       }                                                │ │ │
│  │       messages_list.append(tool_result)               │ │ │
│  │                                                          │ │ │
│  │   ELSE:  # Just text, no tool calls                    │ │ │
│  │     message = {                                        │ │ │
│  │       "role": "assistant",                             │ │ │
│  │       "content": "".join(p.content for p in text_parts)│ │ │
│  │     }                                                  │ │ │
│  │     messages_list.append(message)                      │ │ │
│  └─────────────────────────────────────────────────────────┘ │ │
│                          │                                    │ │
└──────────────────────────┼────────────────────────────────────┘ │ │
                           │                                       │ │
                           ▼                                       │ │
┌──────────────────────────────────────────────────────────────┐  │ │
│           OPENAI API FORMAT (messages_list)                 │  │ │
├──────────────────────────────────────────────────────────────┤  │ │
│                                                              │  │ │
│ Example:                                                     │  │ │
│ [                                                            │  │ │
│   {                                                          │  │ │
│     "role": "user",                                         │  │ │
│     "content": "Can you list files in /tmp?"                │  │ │
│   },                                                         │  │ │
│   {                                                          │  │ │
│     "role": "assistant",                                    │  │ │
│     "content": "I'll list the files for you.",              │  │ │
│     "tool_calls": [                                         │  │ │
│       {                                                     │  │ │
│         "id": "call_abc123",                                │  │ │
│         "type": "function",                                 │  │ │
│         "function": {                                       │  │ │
│           "name": "run_bash",                               │  │ │
│           "arguments": "{\"command\": \"ls /tmp\"}"         │  │ │
│         }                                                   │  │ │
│       }                                                     │  │ │
│     ]                                                       │  │ │
│   },                                                         │  │ │
│   {                                                          │  │ │
│     "role": "tool",                                         │  │ │
│     "tool_call_id": "call_abc123",                          │  │ │
│     "content": "file1.txt\nfile2.py\n..."                   │  │ │
│   },                                                         │  │ │
│   {                                                          │  │ │
│     "role": "user",                                         │  │ │
│     "content": "Now read file1.txt"                         │  │ │
│   },                                                         │  │ │
│   ...                                                        │  │ │
│ ]                                                            │  │ │
│                                                              │  │ │
└──────────────────────────────────────────────────────────────┘  │ │
                           │                                       │ │
                           ▼                                       │ │
┌──────────────────────────────────────────────────────────────┐  │ │
│          PASSED TO prompt() FUNCTION                         │  │ │
├──────────────────────────────────────────────────────────────┤  │ │
│                                                              │  │ │
│ async def prompt(                                            │  │ │
│     prompt_input: PromptInput,                              │  │ │
│     bus: Bus,                                               │  │ │
│     *,                                                      │  │ │
│     history: list[dict[str, Any]] | None = None,  ◄────────┼──┼─┘
│     debug: bool = False,                                    │  │
│ ) -> AsyncGenerator[PromptEvent, None]:                     │  │
│     ...                                                      │  │
│     # Line 172: messages = list(history or [])              │  │
│     messages: list[dict[str, Any]] = list(history or [])    │  │
│     messages.append({"role": "user", "content": user_text}) │  │
│     ...                                                      │  │
│                                                              │  │
└──────────────────────────────────────────────────────────────┘  │
                           │                                       │
                           ▼                                       │
┌──────────────────────────────────────────────────────────────┐  │
│         SENT TO LLM WITH TOOL CALLS                         │  │
├──────────────────────────────────────────────────────────────┤  │
│                                                              │  │
│ StreamInput {                                                │  │
│   model: Model,                                             │  │
│   messages: iter_messages,  ← With/without system reminder  │  │
│   system: [...],            ← System prompt                 │  │
│   tools: [...],             ← Available tools               │  │
│ }                                                            │  │
│                                                              │  │
└──────────────────────────────────────────────────────────────┘  │
                           │                                       │
                           ▼                                       │
┌──────────────────────────────────────────────────────────────┐  │
│      AGENTIC LOOP: Tool Execution & Response Generation      │  │
├──────────────────────────────────────────────────────────────┤  │
│                                                              │  │
│ 1. LLM returns text + tool calls                            │  │
│ 2. Tools are executed                                        │  │
│ 3. build_tool_results_messages(parts) is called  ◄──────────┼──┘
│    Returns: [assistant_msg_with_calls, tool_result_msg, ...]│
│ 4. messages.extend(tool_messages)                           │  
│ 5. Loop continues with updated messages                     │  
│                                                              │  
└──────────────────────────────────────────────────────────────┘  
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│      DONE EVENT & HISTORY RETURN                             │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ yield PromptEvent(type="done", data={                       │
│   ...                                                        │
│   "messages": messages,  ← Complete conversation history    │
│   ...                                                        │
│ })                                                           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼ (CLI main.py:843-844)
                  conversation_history.clear()
                  conversation_history.extend(done_data["messages"])
                           │
                           ▼ (Next turn)
                  prompt(..., history=conversation_history)
                           │
                           └─── REPEATS THE ENTIRE CYCLE ───┘
```

## Key Transformation Points

### 1. Database → Python (Automatic)
```
MessageTable.role        → str ("user" | "assistant")
MessageTable.system      → json.loads() → list[str]
PartTable.state         → json.loads() → dict[str, Any]
PartTable.content       → str (text content or null)
```

### 2. Python → OpenAI Format (Manual Conversion)
```
MessageTable + PartTable[] → dict with:
  - role: "user" | "assistant" | "tool"
  - content: str | None
  - tool_calls: [...] (optional)
  - tool_call_id: str (for tool messages only)
```

### 3. Critical Details
- **Tool arguments**: MUST be `json.dumps(dict)`, not dict
- **ToolCallId matching**: In tool_calls array and tool result messages
- **Content can be None**: For assistant messages with only tool calls
- **Message ordering**: Assistant (with calls) → Tool results → Next user msg
- **Auto-deserialization**: Access `part.state` property, not `part._state`

## Server Route for Database Loading
```
GET /session/{session_id}/messages
Returns: list[{id, role, parts: [...], ...}]
NOT in OpenAI format - must convert manually
```
