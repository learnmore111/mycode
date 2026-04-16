# Conversation History Reconstruction Guide

## Overview

This directory contains comprehensive analysis of how to rebuild conversation history from the database for the opencode_py server routes.

**Analysis Date:** April 16, 2026  
**Project:** `/Users/lihuijin/Desktop/code-agent/opencode_py`

## Documentation Files

### 1. **CONVERSATION_HISTORY_ANALYSIS.md** (Detailed Reference)
   **Use this for:** Complete understanding and implementation details
   
   Covers:
   - Database schema (MessageTable, PartTable)
   - PartTable.state column type and structure
   - Required OpenAI message format
   - Tool call message specification
   - CLI conversation history flow
   - Existing utilities analysis
   - Implementation notes for server routes

### 2. **CONVERSATION_HISTORY_QUICK_REFERENCE.md** (Quick Lookup)
   **Use this for:** Fast reference while coding
   
   Covers:
   - PartTable.state structure (compact)
   - All message formats (7 formats)
   - Message flow diagram
   - Tool message building (reference code)
   - Database query pattern
   - Key file locations
   - Critical notes

### 3. **MESSAGE_FLOW_DIAGRAM.md** (Visual Flow)
   **Use this for:** Understanding the complete architecture
   
   Shows:
   - Database → Python → OpenAI transformations
   - Complete message flow cycle
   - Conversion points and transformations
   - Tool message example
   - Data structure relationships

### 4. **ANALYSIS_SUMMARY.txt** (This File's Context)
   **Use this for:** Executive summary and quick facts

---

## Quick Answers to Your Questions

### Q1: PartTable.state Column Type?
**A:** Text (SQLAlchemy) → SQLite TEXT field with JSON string  
Auto-deserialized to `dict[str, Any]` via property  
**Location:** `opencode/storage/models.py:126-140`

### Q2: Message Format for prompt()?
**A:** OpenAI-compatible format with:
- User messages: `{"role": "user", "content": "text"}`
- Assistant messages: `{"role": "assistant", "content": "text", "tool_calls": [...]}`
- Tool messages: `{"role": "tool", "tool_call_id": "...", "content": "..."}`

**Critical:** Tool arguments MUST be JSON string, not dict

### Q3: Tool Message Format?
**A:** See `processor.build_tool_results_messages()` (line 487 in processor.py)

Key: Tool call IDs must match, arguments must be `json.dumps()` string

### Q4: CLI History Flow?
**A:** `prompt()` yields done event with `messages` field → CLI stores in `conversation_history` → passed to next `prompt()` call

### Q5: Existing Reconstruction Utility?
**A:** **No purpose-built function exists**

However, server has: `GET /session/{session_id}/messages`  
Returns raw DB data (not OpenAI format) - requires manual conversion

---

## Key Code Locations

| Task | File | Lines |
|------|------|-------|
| **Schema** | `opencode/storage/models.py` | 87-112, 114-140 |
| **History Entry** | `opencode/session/prompt.py` | 119, 172-173 |
| **Tool Messages** | `opencode/session/processor.py` | 487-522 |
| **CLI Flow** | `opencode/cli/main.py` | 840-849 |
| **Server Endpoint** | `opencode/server/routes/session.py` | 109-174 |
| **Persistence** | `opencode/session/message.py` | 297-329 |

---

## Critical Implementation Details

### ✗ WRONG vs ✓ RIGHT

```python
# ✗ WRONG - arguments as dict
"arguments": {"command": "ls /tmp"}

# ✓ RIGHT - arguments as JSON string
"arguments": '{"command": "ls /tmp"}'
# Implementation: json.dumps(tp.state.get("input", {}))
```

```python
# ✗ WRONG - accessing raw state
part._state  # Returns JSON string

# ✓ RIGHT - using property auto-deserialization
part.state   # Returns dict[str, Any]
```

```python
# ✗ WRONG - content must not be different types
{"role": "assistant", "content": None, "tool_calls": [...]}  # if no text

# ✓ RIGHT - content can be null for tool-only messages
{"role": "assistant", "content": None, "tool_calls": [...]}  # OK
```

---

## Building Conversation History from Database

### Step 1: Query Messages
```python
from opencode.storage.database import get_session as get_db_session
from opencode.storage.models import MessageTable, PartTable

db = get_db_session()
messages = (
    db.query(MessageTable)
    .filter(MessageTable.session_id == session_id)
    .order_by(MessageTable.time_created)
    .all()
)

message_ids = [m.id for m in messages]
parts = (
    db.query(PartTable)
    .filter(PartTable.message_id.in_(message_ids))
    .order_by(PartTable.time_created)
    .all()
)
```

### Step 2: Convert to OpenAI Format
```python
conversation_history = []

# Organize parts by message
parts_by_msg = {}
for p in parts:
    parts_by_msg.setdefault(p.message_id, []).append(p)

# Build conversation history
for msg in messages:
    msg_parts = parts_by_msg.get(msg.id, [])
    
    if msg.role == "user":
        # Extract text content
        text_part = next((p for p in msg_parts if p.type == "text"), None)
        content = text_part.content if text_part else ""
        conversation_history.append({
            "role": "user",
            "content": content
        })
    
    elif msg.role == "assistant":
        text_parts = [p for p in msg_parts if p.type == "text"]
        tool_parts = [p for p in msg_parts if p.type == "tool"]
        
        if tool_parts:
            # Assistant message with tool calls
            conversation_history.append({
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
                    }
                    for tp in tool_parts
                ]
            })
            
            # Tool result messages
            for tp in tool_parts:
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tp.tool_call_id,
                    "content": tp.state.get("output", "")
                })
        else:
            # Simple text message
            conversation_history.append({
                "role": "assistant",
                "content": "".join(p.content for p in text_parts)
            })
```

### Step 3: Pass to prompt()
```python
from opencode.session.prompt import prompt, PromptInput

history = conversation_history  # From above

prompt_input = PromptInput(
    session_id=session_id,
    parts=[{"type": "text", "content": user_message}]
)

async for event in prompt(prompt_input, bus, history=history):
    # Handle events
    pass
```

---

## PartTable.state Structure

For ToolPart records, state contains:

```python
{
    "status": "pending|running|completed|error",
    "input": {...},         # Tool arguments (dict, pre-parsed JSON)
    "output": "...",        # Tool execution result (string)
    "is_error": bool,       # Whether tool failed
    "title": "...",         # Human-readable title (optional)
    "message": "...",       # Additional message (optional)
    "metadata": {...},      # Additional metadata (optional)
    "_raw_args": "...",     # Internal: raw JSON string during parsing
    "_parse_error": "..."   # Internal: error if JSON parsing failed
}
```

Access via property (auto-deserialized):
```python
tool_state = part.state  # dict[str, Any]
arguments = tool_state.get("input", {})  # Already parsed dict
output = tool_state.get("output", "")    # String
```

---

## Message Format Reference

### User Message
```json
{
  "role": "user",
  "content": "What can you do?"
}
```

### Assistant Text Message
```json
{
  "role": "assistant",
  "content": "I can help with various tasks..."
}
```

### Assistant Message with Tool Calls
```json
{
  "role": "assistant",
  "content": "I'll check that for you.",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "run_bash",
        "arguments": "{\"command\": \"ls -la /tmp\"}"
      }
    }
  ]
}
```

### Tool Result Message
```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "file1.txt\nfile2.txt\n..."
}
```

---

## Common Pitfalls

1. **Tool arguments as dict instead of JSON string**
   - ✗ `"arguments": {"key": "value"}`
   - ✓ `"arguments": json.dumps({"key": "value"})`

2. **Accessing raw state column**
   - ✗ `part._state` (returns JSON string)
   - ✓ `part.state` (property returns dict)

3. **Tool call ID mismatch**
   - Ensure tool_call_id in tool_calls array matches tool_call_id in tool result

4. **Wrong message order**
   - ✗ Tool result, then assistant with calls
   - ✓ Assistant with calls, then tool results

5. **Not using JSON string for arguments**
   - Will fail when API validates the message format

---

## Testing Your Implementation

1. Query a session's messages: `GET /session/{session_id}/messages`
2. Convert to conversation history using Step 2 above
3. Pass to prompt() function with new user message
4. Verify events are generated (text_delta, tool_start, etc.)
5. Compare final done event's messages with your reconstructed history

---

## Related Files

- **Database Schema:** `opencode/storage/models.py`
- **Message Models:** `opencode/session/message.py`
- **Prompt Entry:** `opencode/session/prompt.py`
- **Tool Processing:** `opencode/session/processor.py`
- **Server API:** `opencode/server/routes/session.py`
- **CLI Integration:** `opencode/cli/main.py`

---

## For More Details

See:
- `CONVERSATION_HISTORY_ANALYSIS.md` - Full analysis with all code references
- `CONVERSATION_HISTORY_QUICK_REFERENCE.md` - Quick lookup guide
- `MESSAGE_FLOW_DIAGRAM.md` - Complete flow visualization
