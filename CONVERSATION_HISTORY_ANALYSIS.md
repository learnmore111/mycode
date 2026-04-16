# Conversation History Reconstruction Analysis
## opencode_py Project

---

## 1. DATABASE SCHEMA ANALYSIS

### PartTable.state Column Type
**Location:** `opencode/storage/models.py:126`

```python
_state = Column("state", Text, nullable=True)  # JSON (tool state)

@property
def state(self) -> dict[str, Any] | None:
    try:
        return json.loads(self._state) if self._state else None
    except (json.JSONDecodeError, TypeError):
        return None

@state.setter
def state(self, value: dict[str, Any] | None) -> None:
    self._state = json.dumps(value) if value else None
```

**Key Findings:**
- **Column Type:** `Text` (SQLAlchemy) → stored as JSON string in SQLite
- **Auto-deserialization:** Property `state` automatically deserializes JSON to `dict[str, Any]`
- **Format:** JSON-serialized dictionary (handled via the property getter/setter)
- **ToolPart.state Structure:**
  - `status`: "pending" | "running" | "completed" | "error"
  - `input`: dict (tool arguments, JSON-parsed from args)
  - `output`: str (tool execution result)
  - `is_error`: bool
  - `title`: str (optional)
  - `message`: str (optional, additional message)
  - `metadata`: dict (optional, e.g., `{"cached": True}`)
  - `_raw_args`: str (optional, raw args JSON string during parsing)
  - `_parse_error`: str (optional, if JSON parsing failed)

### MessageTable Schema
**Location:** `opencode/storage/models.py:87-112`

```python
class MessageTable(Base):
    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)  # "user" | "assistant"
    parent_id = Column(String, nullable=True)
    format = Column(Text, nullable=True)  # JSON (format schema)
    # Assistant-specific
    model_id = Column(String, nullable=True)
    provider_id = Column(String, nullable=True)
    agent = Column(String, nullable=True)
    variant = Column(String, nullable=True)
    system = Column(Text, nullable=True)  # JSON array of system prompts
    error = Column(Text, nullable=True)  # JSON error object
    # Tokens / Cost
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    tokens_reasoning = Column(Integer, nullable=True)
    tokens_cache_read = Column(Integer, nullable=True)
    tokens_cache_write = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    # Time
    time_created = Column(Integer, nullable=False)
    time_completed = Column(Integer, nullable=True)
```

---

## 2. REQUIRED MESSAGE FORMAT FOR LLM API

### Location & Context
**File:** `opencode/session/prompt.py:172-173`

```python
# Build conversation messages
messages: list[dict[str, Any]] = list(history or [])
messages.append({"role": "user", "content": user_text})
```

The `history` parameter is expected to be OpenAI-compatible message format.

### OpenAI Message Format Required
All messages passed to the LLM must follow OpenAI format:

**User Messages:**
```python
{
    "role": "user",
    "content": "<string>"  # Plain text or can be list of content blocks
}
```

**Assistant Messages (with text only):**
```python
{
    "role": "assistant",
    "content": "<string>"  # Text response
}
```

**Assistant Messages (with tool calls):**
```python
{
    "role": "assistant",
    "content": "<string or null>",  # Can be null if only tool calls
    "tool_calls": [
        {
            "id": "<tool_call_id>",
            "type": "function",
            "function": {
                "name": "<tool_name>",
                "arguments": "<JSON string>"  # IMPORTANT: JSON-serialized string, not dict!
            }
        }
    ]
}
```

**Tool Result Messages:**
```python
{
    "role": "tool",
    "tool_call_id": "<tool_call_id>",
    "content": "<string>"  # Tool output/result
}
```

---

## 3. TOOL CALL MESSAGE STRUCTURE (Reference Implementation)

### Location & Reference
**File:** `opencode/session/processor.py:487-522` - `build_tool_results_messages()`

This function builds the correct OpenAI format after tool execution:

```python
def build_tool_results_messages(parts: list[Part]) -> list[dict[str, Any]]:
    """Convert tool parts to assistant + tool_result messages for the next LLM call."""
    tool_calls = [p for p in parts if isinstance(p, ToolPart)]
    if not tool_calls:
        return []

    assistant_tool_calls = []
    for tp in tool_calls:
        assistant_tool_calls.append({
            "id": tp.tool_call_id,
            "type": "function",
            "function": {
                "name": tp.tool,
                "arguments": json.dumps(tp.state.get("input", {}))  # KEY: JSON string!
            },
        })

    messages: list[dict[str, Any]] = []

    # Build assistant message with tool calls
    text_parts = [p for p in parts if isinstance(p, TextPart)]
    text_content = "".join(p.content for p in text_parts)
    messages.append({
        "role": "assistant",
        "content": text_content or None,
        "tool_calls": assistant_tool_calls,
    })

    # Build tool result messages for each tool call
    for tp in tool_calls:
        output = tp.state.get("output", "")
        tool_message = tp.state.get("message", "")
        if tool_message:
            output = f"{output}\n\n{tool_message}"
        messages.append({
            "role": "tool",
            "tool_call_id": tp.tool_call_id,
            "content": output,
        })

    return messages
```

**Critical Details:**
1. **Tool call ID:** Must match exactly (`tp.tool_call_id`)
2. **Function arguments:** MUST be JSON-serialized string (`json.dumps(tp.state.get("input", {}))`)
3. **Tool output format:** String with optional additional message appended
4. **Text content:** Can be None, empty string, or the actual text
5. **Message ordering:** Assistant message with tool_calls FIRST, then tool result messages

---

## 4. CLI CONVERSATION HISTORY BUILDING

### Location
**File:** `opencode/cli/main.py:840-849`

```python
# Keep conversation history (use full messages from prompt if available)
if done_data.get("messages"):
    # Use the complete messages from the agentic loop (includes tool calls/results)
    conversation_history.clear()
    conversation_history.extend(done_data["messages"])
else:
    # Fallback: simple text-only history
    conversation_history.append({"role": "user", "content": text})
    if full_text:
        conversation_history.append({"role": "assistant", "content": full_text})
```

**Flow:**
1. The `done_data` event from `prompt()` contains `"messages"` field
2. This is the complete conversation history including all tool calls and results
3. It's passed to the NEXT `prompt()` call as the `history` parameter
4. On next turn: `async for event in prompt(inp, bus, history=conversation_history, ...)`

**Where `done_data["messages"]` comes from:**
- **Location:** `opencode/session/prompt.py:389-411`
- **Content:** The `messages` list variable that was maintained throughout the agentic loop
- **Structure:** Starts with OpenAI format, gets extended with `build_tool_results_messages()` output
- **Includes:** ALL turns including tool calls and results

---

## 5. EXISTING RECONSTRUCTION UTILITY

### NO PURPOSE-BUILT RECONSTRUCTION FUNCTION EXISTS

However, there IS a generic API endpoint that loads messages from DB:

### Server Route: GET /session/{session_id}/messages
**Location:** `opencode/server/routes/session.py:109-174`

```python
@router.get("/{session_id}/messages")
async def session_messages(session_id: str, directory: str = Query(default=".")):
    """Get all messages and their parts for a session."""
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import MessageTable, PartTable

    async def _fn():
        db = get_db_session()
        try:
            # Query all messages
            messages = (
                db.query(MessageTable)
                .filter(MessageTable.session_id == session_id)
                .order_by(MessageTable.time_created)
                .all()
            )
            if not messages:
                return []

            # Query all parts for those messages
            message_ids = [m.id for m in messages]
            parts = (
                db.query(PartTable)
                .filter(PartTable.message_id.in_(message_ids))
                .order_by(PartTable.time_created)
                .all()
            )

            # Organize parts by message
            parts_by_msg: dict[str, list[dict[str, Any]]] = {}
            for p in parts:
                parts_by_msg.setdefault(p.message_id, []).append({
                    "id": p.id,
                    "type": p.type,
                    "content": p.content,
                    "tool": p.tool,
                    "toolCallId": p.tool_call_id,
                    "state": p.state,  # Automatically deserializes JSON
                    "time": {"created": p.time_created, "completed": p.time_completed},
                })

            # Build result
            result = []
            for m in messages:
                result.append({
                    "id": m.id,
                    "sessionId": m.session_id,
                    "role": m.role,
                    "parentId": m.parent_id,
                    "modelId": m.model_id,
                    "providerId": m.provider_id,
                    "agent": m.agent,
                    "tokens": {...},
                    "cost": m.cost,
                    "error": json.loads(m.error) if m.error else None,
                    "parts": parts_by_msg.get(m.id, []),
                    "time": {"created": m.time_created, "completed": m.time_completed},
                })
            return result
        finally:
            db.close()

    return await provide(directory, _fn)
```

**This endpoint returns:**
- Raw message and part data from DB
- NOT in OpenAI format
- NOT converted to conversation history

**To convert to OpenAI format, you would need to:**
1. Query messages via this endpoint OR directly via DB
2. For each message with role="user": `{"role": "user", "content": "..."}`
3. For each message with role="assistant":
   - Collect all TextPart and ToolPart contents
   - If there are ToolPart instances:
     - Build tool_calls array from ToolPart data
     - Return assistant message with tool_calls
     - Add tool result messages from tp.state["output"]
   - Otherwise, simple text-only assistant message

---

## SUMMARY TABLE

| Aspect | Details |
|--------|---------|
| **PartTable.state Type** | `Text` column with JSON string value; property auto-deserializes to `dict[str, Any]` |
| **state Structure** | `{status, input, output, is_error, title, message, metadata, _raw_args, _parse_error}` |
| **User Message Format** | `{"role": "user", "content": "<text>"}` |
| **Assistant Message Format** | `{"role": "assistant", "content": "<text or null>", "tool_calls": [...]}` |
| **Tool Call Format** | `{"id": "<id>", "type": "function", "function": {"name": "<name>", "arguments": "<JSON string>"}}` |
| **Tool Result Format** | `{"role": "tool", "tool_call_id": "<id>", "content": "<output>"}` |
| **Entry Point** | `opencode.session.prompt.prompt(prompt_input, bus, history=<list>, ...)` |
| **History Passed As** | Parameter `history: list[dict[str, Any]] \| None = None` (line 119) |
| **History Built From** | `done_data["messages"]` from previous turn (line 408 in prompt.py) |
| **Existing Reconstruction Utility** | None purpose-built; use server API endpoint `/session/{id}/messages` + custom conversion |
| **Tool Message Building** | Reference impl: `processor.build_tool_results_messages()` (line 487) |

---

## IMPLEMENTATION NOTES FOR SERVER ROUTE RECONSTRUCTION

If building a route to reconstruct conversation history from DB:

1. **Query Messages:**
   ```python
   db.query(MessageTable)
       .filter(MessageTable.session_id == session_id)
       .order_by(MessageTable.time_created)
       .all()
   ```

2. **Get Parts:**
   ```python
   db.query(PartTable)
       .filter(PartTable.message_id.in_(message_ids))
       .order_by(PartTable.time_created)
       .all()
   ```

3. **Convert User Message:**
   ```python
   if msg.role == "user":
       return {"role": "user", "content": "<first text part content>"}
   ```

4. **Convert Assistant Message:**
   ```python
   if msg.role == "assistant":
       text_parts = [p for p in parts if p.type == "text"]
       tool_parts = [p for p in parts if p.type == "tool"]
       
       if tool_parts:
           # Build assistant message with tool_calls
           assistant_msg = {
               "role": "assistant",
               "content": "".join(p.content for p in text_parts) or None,
               "tool_calls": [
                   {
                       "id": tp.tool_call_id,
                       "type": "function",
                       "function": {
                           "name": tp.tool,
                           "arguments": json.dumps(tp.state.get("input", {}))
                       }
                   } for tp in tool_parts
               ]
           }
           # Return both assistant and tool result messages
           messages = [assistant_msg]
           for tp in tool_parts:
               messages.append({
                   "role": "tool",
                   "tool_call_id": tp.tool_call_id,
                   "content": tp.state.get("output", "")
               })
           return messages
       else:
           return {"role": "assistant", "content": "".join(p.content for p in text_parts)}
   ```

5. **Flatten into single list** and return as conversation history

