# Quick Reference: Conversation History Reconstruction

## 1. PartTable.state Column Type
```
Database Column: Text (SQLAlchemy) → SQLite TEXT field with JSON string
Python Type: dict[str, Any] (via property auto-deserialization)

Structure for ToolPart.state:
{
    "status": "pending|running|completed|error",
    "input": {...},              # Tool arguments (already JSON-parsed)
    "output": "...",             # Tool execution result
    "is_error": bool,
    "title": "...",              # Optional
    "message": "...",            # Optional additional message
    "metadata": {...},           # Optional, e.g. {"cached": true}
    "_raw_args": "...",          # Internal: raw JSON string during parsing
    "_parse_error": "..."        # Internal: error during JSON parsing
}
```

## 2. OpenAI Message Format (Required)

### User Message
```python
{"role": "user", "content": "text"}
```

### Assistant Message (no tool calls)
```python
{"role": "assistant", "content": "text"}
```

### Assistant Message (with tool calls)
```python
{
    "role": "assistant",
    "content": "text or null",
    "tool_calls": [
        {
            "id": "call_xyz123",
            "type": "function",
            "function": {
                "name": "tool_name",
                "arguments": '{"key": "value"}'  # JSON STRING, not dict!
            }
        }
    ]
}
```

### Tool Result Message
```python
{
    "role": "tool",
    "tool_call_id": "call_xyz123",
    "content": "tool output"
}
```

## 3. Message Flow

```
User Input
    ↓
prompt() entry: history parameter = list[dict[str, Any]]
    ↓
messages = list(history or [])  # Line 172 in prompt.py
messages.append({"role": "user", "content": user_text})
    ↓
Send to LLM with tool_calls
    ↓
build_tool_results_messages(parts) → builds proper OpenAI format
    ↓
messages.extend(tool_messages)
    ↓
Next iteration
    ↓
Done → yield PromptEvent(type="done", data={"messages": messages})
    ↓
CLI: conversation_history.extend(done_data["messages"])
    ↓
Next call to prompt(..., history=conversation_history)
```

## 4. Building Tool Call Messages (Reference)

Use `processor.build_tool_results_messages(parts)` as template:

```python
# Tool calls in assistant message
tool_calls = [
    {
        "id": tp.tool_call_id,
        "type": "function",
        "function": {
            "name": tp.tool,
            "arguments": json.dumps(tp.state.get("input", {}))  # ← JSON STRING!
        }
    }
]

# Tool results after assistant message
for tp in tool_calls:
    messages.append({
        "role": "tool",
        "tool_call_id": tp.tool_call_id,
        "content": tp.state.get("output", "")
    })
```

## 5. Reconstructing from Database

### Query Pattern
```python
from opencode.storage.database import get_session as get_db_session
from opencode.storage.models import MessageTable, PartTable

db = get_db_session()
try:
    messages = (
        db.query(MessageTable)
        .filter(MessageTable.session_id == session_id)
        .order_by(MessageTable.time_created)
        .all()
    )
    
    if messages:
        message_ids = [m.id for m in messages]
        parts = (
            db.query(PartTable)
            .filter(PartTable.message_id.in_(message_ids))
            .order_by(PartTable.time_created)
            .all()
        )
finally:
    db.close()
```

### Conversion Logic
1. For each message with `role="user"`:
   - Extract first TextPart's content
   - Return: `{"role": "user", "content": "..."}`

2. For each message with `role="assistant"`:
   - Get all TextPart (concat content)
   - Get all ToolPart
   - If ToolPart exists:
     - Return assistant message with tool_calls
     - Return tool result messages for each ToolPart
   - Else:
     - Return simple text message

## 6. Important Notes

### Critical Details
- **PartTable.state is JSON string**: Access via `part.state` property (auto-deserializes)
- **Tool arguments MUST be JSON string**: `json.dumps(tp.state.get("input", {}))` not a dict
- **ToolCallId matching**: IDs in tool_calls and tool result messages must match exactly
- **Message ordering**: Assistant with tool_calls FIRST, then tool results
- **Content can be None**: When assistant only makes tool calls, content can be null

### No Built-in Reconstruction Function
- There's no `load_conversation_history()` function
- Use the server endpoint `/session/{id}/messages` as data source
- Then convert DB records to OpenAI format using the conversion logic above

## 7. Key File Locations

| File | Purpose |
|------|---------|
| `opencode/storage/models.py` | Schema definitions (MessageTable, PartTable) |
| `opencode/session/prompt.py:172-173` | Where history is used |
| `opencode/session/prompt.py:408` | Where messages are returned in done event |
| `opencode/session/processor.py:487-522` | Reference tool message building |
| `opencode/cli/main.py:840-849` | CLI conversation history flow |
| `opencode/server/routes/session.py:109-174` | Endpoint to load messages from DB |
| `opencode/session/message.py:297-329` | Message persistence helpers |

