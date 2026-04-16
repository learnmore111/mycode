# Compaction Code Trace - Detailed Flow

## 1. Trigger: When Should Compaction Happen?

### File: `opencode/session/prompt.py` (lines 246-254)

```python
# Fallback to 32K if model context limit is not configured (prevents unbounded growth)
context_limit = model.limit.context if model.limit.context > 0 else 32_000
if context_limit > 0 and compaction.should_compact(
    messages=messages, model_context=context_limit
):
    logger.info("context overflow detected, compacting")
    messages = await compaction.compact(messages, **compact_kwargs)
    yield PromptEvent(type="compact", data={"session_id": session_id})
```

**Decision Logic:**
```python
# In compaction.py (lines 72-78)
def should_compact(*, messages, model_context):
    if model_context <= 0:
        return False
    est = estimate_messages_tokens(messages)
    threshold = int(model_context * OVERFLOW_RATIO)  # 0.85 = 85%
    return est > threshold
```

**Example:**
- Context limit: 100K tokens
- Threshold: 85K tokens
- Current messages: 86K tokens → Compact!

---

## 2. Compact Phase 1: Prune Tool Outputs

### File: `opencode/session/compaction.py` (lines 87-133)

```python
def prune_tool_outputs(messages):
    """Prune old tool outputs to free context space."""
    tool_indices = []  # (msg_idx, estimated_tokens)
    turns = 0

    # Walk backwards, collecting tool messages beyond last 2 turns
    for msg_idx in range(len(messages) - 1, -1, -1):
        msg = messages[msg_idx]
        if msg.get("role") == "user":
            turns += 1
        if turns < 2:
            continue  # Protect last 2 turns

        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if content and content != "[Old tool result content cleared]":
                est = estimate_tokens(content)
                tool_indices.append((msg_idx, est))

    # Protect first PRUNE_PROTECT (40K) tokens
    protected_tokens = 0
    prunable = []
    for msg_idx, est in tool_indices:
        protected_tokens += est
        if protected_tokens > PRUNE_PROTECT:
            prunable.append((msg_idx, est))

    # Replace old tool outputs with placeholder
    pruned = 0
    for msg_idx, est in prunable:
        messages[msg_idx]["content"] = "[Old tool result content cleared]"
        pruned += est

    if pruned > PRUNE_MINIMUM:
        logger.info("pruned tool outputs", count=len(prunable), tokens_freed=pruned)
        return messages, pruned

    return messages, 0
```

**Example Flow:**
```
Messages: [turn1_Q, turn1_A, turn1_tool(5K), turn2_Q, turn2_A, turn2_tool(3K), turn3_Q, turn3_A, turn3_tool(2K)]

Step 1: Identify turns
  turns = 0 at end, walking backwards
  Index 8: turn3_A → turns = 1 (protect)
  Index 7: turn3_Q → turns = 1 (protect)
  Index 6: turn3_tool → turns = 1 (protect)
  Index 5: turn2_A → turns = 2 (protect)
  Index 4: turn2_Q → turns = 2 (protect)
  Index 3: turn2_tool → turns = 2 (protect)
  Index 2: turn1_tool → turns = 3 (PRUNE!)
  Index 1: turn1_A → turns = 3
  Index 0: turn1_Q → turns = 3

Step 2: Collect prunable
  Index 2: turn1_tool(5K) is old, add to prunable
  Result: pruned = 5K tokens

Result:
  messages[2]["content"] = "[Old tool result content cleared]"
```

**Exit Condition:**
```python
if freed > PRUNE_MINIMUM:  # 20K
    logger.info("pruning freed enough tokens, skipping full compaction", freed=freed)
    return messages  # ← Early return! No full compaction needed
```

---

## 3. Compact Phase 2: Split by Turns (if pruning insufficient)

### File: `opencode/session/compaction.py` (lines 136-160)

```python
def _split_by_turns(messages, keep_turns=COMPACT_KEEP_TURNS):
    """Split messages into (old, recent) by user turns.
    
    A "turn" starts at each role=user message.
    Keep the last N turns, summarize everything before.
    """
    # Find indices where user turns start
    turn_starts = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            turn_starts.append(i)

    if len(turn_starts) <= keep_turns:
        # Not enough turns to split
        return [], list(messages)  # Nothing to summarize

    split_idx = turn_starts[-keep_turns]  # Index of oldest kept turn
    return list(messages[:split_idx]), list(messages[split_idx:])
```

**Example Flow (COMPACT_KEEP_TURNS = 3):**
```
Messages layout:
  0: user (turn 1)         ← Turn starts[0]
  1: assistant
  2: tool
  3: user (turn 2)         ← Turn starts[1]
  4: assistant
  5: tool
  6: user (turn 3)         ← Turn starts[2]
  7: assistant
  8: tool
  9: user (turn 4)         ← Turn starts[3]
  10: assistant
  11: tool
  12: user (turn 5)        ← Turn starts[4]
  13: assistant
  14: tool
  15: user (turn 6)        ← Turn starts[5]
  16: assistant
  17: tool

turn_starts = [0, 3, 6, 9, 12, 15]
keep_turns = 3
split_idx = turn_starts[-3] = turn_starts[3] = 9

Result:
  old = messages[0:9]     = turns 1-3 (16 messages to summarize)
  recent = messages[9:]   = turns 4-6 (9 messages to keep)
```

---

## 4. Compact Phase 3: Truncate Tool Outputs for Summary Call

### File: `opencode/session/compaction.py` (lines 168-191)

```python
def _truncate_tool_outputs_for_summary(messages, limit=SUMMARY_TOOL_OUTPUT_LIMIT):
    """Create a DEEP COPY with large tool outputs truncated.
    
    This is used ONLY for the compaction LLM call.
    The original messages are never modified.
    """
    truncated = copy.deepcopy(messages)  # ← Creates new objects!
    for msg in truncated:
        # Truncate tool result content
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > limit:
                msg["content"] = content[:limit] + f"\n... [truncated, {len(content)} chars total]"

        # Truncate tool_call arguments in assistant messages
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if len(args) > limit:
                fn["arguments"] = args[:limit] + "..."
    return truncated
```

**Key Point:** This is a DEEP COPY - original `old` messages are unchanged.

---

## 5. Compact Phase 4: Call LLM for Summary

### File: `opencode/session/compaction.py` (lines 302-318)

```python
compaction_prompt = await _load_compaction_prompt()  # Load compaction agent's prompt

summary_messages = list(truncated_old)  # Use truncated copy
summary_messages.append({"role": "user", "content": compaction_prompt})

stream_input = llmmod.StreamInput(
    model=model,
    messages=summary_messages,
    system=system,  # ← SAME system prompt as main agent → cache hit!
    tools=tools,    # ← SAME tools as main agent → cache hit!
    tool_choice="none",  # prevent tool calls
    temperature=0.0,
    max_tokens=4096,
    api_key=api_key,
    api_base=api_base,
)
```

**Compaction Prompt Loaded From:**
```python
async def _load_compaction_prompt():
    try:
        agent = await agentmod.get("compaction")  # Special agent
        if agent and agent.prompt:
            return agent.prompt
    except:
        pass
    return _COMPACTION_PROMPT_FALLBACK  # Fallback default
```

**Fallback Prompt:**
```python
_COMPACTION_PROMPT_FALLBACK = """Provide a detailed summary of the conversation so far.
Focus on: what was done, what is being worked on, which files are relevant,
what needs to be done next, and key user requests or constraints."""
```

---

## 6. Compact Phase 5: Extract Summary

### File: `opencode/session/compaction.py` (lines 194-225)

```python
def _extract_summary(text, max_length=8000):
    """Extract <summary> block and strip <analysis> scratchpad."""
    # Try to find <summary>...</summary>
    match = re.search(r"<summary>(.*?)</summary>", text, re.DOTALL)
    if match:
        summary = match.group(1).strip()
        if len(summary) > max_length:
            summary = summary[:max_length] + "\n... [summary truncated]"
        return summary

    # Fallback: strip <analysis>...</analysis> and return rest
    stripped = re.sub(r"<analysis>.*?</analysis>", "", text, flags=re.DOTALL)
    stripped = stripped.strip()
    if stripped and len(stripped) < len(text):
        if len(stripped) > max_length:
            stripped = stripped[:max_length] + "\n... [summary truncated]"
        return stripped

    # Last resort: truncate
    result = text.strip()
    if len(result) > max_length:
        result = result[:max_length] + "\n... [summary truncated]"
        logger.warn("summary extraction fell back to truncated full text", length=len(text))
    return result if result else "[Empty summary generated]"
```

**Example:**
```
LLM Output:
  <analysis>
  The conversation shows the user working on a web application...
  Multiple iterations of building features...
  </analysis>
  
  <summary>
  Built a React web app with authentication, now working on dashboard features.
  Recent work: API integration, database schema design. Next: implement user profiles.
  </summary>

Extract Result:
  "Built a React web app with authentication, now working on dashboard features.
   Recent work: API integration, database schema design. Next: implement user profiles."
```

---

## 7. Compact Phase 6: Build Result

### File: `opencode/session/compaction.py` (lines 228-241)

```python
def _build_compact_result(summary, recent):
    """Assemble the compacted message list.
    
    The summary is injected as a USER message (not system)
    so the main agent's system prompt prefix remains identical
    → prefix cache hit on next call.
    """
    user_summary_msg = {
        "role": "user",
        "content": COMPACT_USER_MSG_TEMPLATE.format(summary=summary),
    }
    return [user_summary_msg, *recent]
```

**Template Used:**
```python
COMPACT_USER_MSG_TEMPLATE = """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

{summary}

Recent messages are preserved verbatim. Continue from where we left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I'll continue" or similar. Pick up the last task as if the break never happened."""
```

**Result Structure:**
```python
[
    {
        "role": "user",
        "content": """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

Built a React web app with authentication, now working on dashboard features...

Recent messages are preserved verbatim. Continue from where we left off..."""
    },
    # ... 3 recent turns (user/assistant/tool messages) ...
]
```

---

## 8. Return to Main Loop

### File: `opencode/session/prompt.py` (lines 249-254)

```python
if context_limit > 0 and compaction.should_compact(messages=messages, model_context=context_limit):
    logger.info("context overflow detected, compacting")
    messages = await compaction.compact(messages, **compact_kwargs)
    # ↑ OLD messages list replaced with NEW compacted list
    
    yield PromptEvent(type="compact", data={"session_id": session_id})
    # ↑ Event sent to frontend
```

**Important:** At this point:
- ✅ The `old` messages that were summarized are now discarded
- ✅ They exist only in the `summary` user message text
- ❌ The original old message list is gone (not returned, not saved, not passed to UI)
- ✅ New `messages` list contains: [summary_msg, *recent_turns]

---

## 9. Context Snapshot Creation (Iteration After Compaction)

### File: `opencode/session/context.py` (lines 156-158)

```python
# After compaction happens, on the NEXT iteration:
snapshot_data = build_context_snapshot(
    system=system,
    tools=tools,
    messages=iter_messages,  # ← Contains [summary_msg, *recent_turns]
    model_id=f"{provider_id}/{model_id}",
    iteration=iteration,
    has_history=bool(history),
    actual_usage={...}
)

# Inside build_context_snapshot:
for idx, msg in enumerate(messages):
    role = msg.get("role", "unknown")
    content = msg.get("content") or ""
    
    # ... build message info ...
    
    # Detect compaction summary
    if role == "user" and _COMPACTION_MARKER in content.lower():
        info["is_compaction_summary"] = True
        compaction_boundary_index = idx

yield PromptEvent(type="context_snapshot", data=snapshot_data)
```

**Detection Mechanism:**
```python
_COMPACTION_MARKER = "continued from a previous conversation"  # (Line 20)

# This is in the COMPACT_USER_MSG_TEMPLATE (line 34):
"This session is being continued from a previous conversation that ran out of context..."
                      ↑ Contains marker!
```

---

## 10. Frontend Event Handling

### File: `web/src/hooks/useChat.ts` (lines 106-108)

```typescript
case 'context_snapshot':
  setContextSnapshot(event.data as unknown as ContextSnapshot)
  break

// MISSING:
// case 'compact':
//   // Not handled!
//   break
```

**Event Type Definition:**
```typescript
// web/src/types/index.ts:100-111
export type SSEEventType =
  | 'started'
  | 'text_delta'
  | 'tool_start'
  | 'tool_running'
  | 'tool_done'
  | 'error'
  | 'compact'          // ← Defined but not handled
  | 'guard_warn'
  | 'guard_stop'
  | 'context_snapshot'
  | 'done'
```

---

## 11. UI Display

### File: `web/src/components/ContextViewer.tsx` (lines 94, 133-250)

```typescript
// Message badge display (line 94)
if (msg.is_compaction_summary) 
  badges.push(<span key="c" className="...">压缩摘要</span>)

// Full component shows:
// • System prompt
// • Tools list
// • Messages (marked if is_compaction_summary)
// • Token usage bar
// • Cache status indicators

// But NOT shown:
// • What was discarded
// • When compaction happened
// • Timeline of compressions
```

---

## 12. Database Persistence

### File: `opencode/session/message.py` (lines 261-295)

```python
def save_message(msg: MessageInfo) -> None:
    """Persist a UserMessage or AssistantMessage."""
    
    if isinstance(msg, UserMessage):
        # ✅ Saves user message including the SUMMARY
        # (The summary is stored as content in a regular user message)
        pass
    
    elif isinstance(msg, AssistantMessage):
        # ✅ Saves assistant message
        pass
    
    # ❌ SystemMessage not saved
    # ❌ No compaction_boundary marker persisted
    # ❌ No old message list preserved
```

**What Gets Saved:**
```python
# The summary message IS saved as a normal user message:
{
    "role": "user",
    "content": "This session is being continued from a previous conversation...\n\nSummary: Built a React web app..."
}

# When reloading session:
rebuild_history_from_db(session_id)  # ← Gets the summary message
# Returns: [summary_msg, ...recent_turns...]
# Old messages still not available
```

---

## Summary of Data Loss Points

| Point | What's Lost | Why | Location |
|-------|------------|-----|----------|
| **Split** | `old` messages list | Not returned from function | `compaction.py:288` |
| **Truncate** | Copy of old messages | Deep copy discarded | `compaction.py:300` |
| **Summary** | Pre-summary message count | Not passed to event | `prompt.py:254` |
| **Event** | Compaction metrics | Minimal data `{session_id}` | `prompt.py:254` |
| **DB** | Compaction record | No table/model | `models.py` |
| **UI** | Compaction feedback | Event not handled | `useChat.ts:106-108` |

