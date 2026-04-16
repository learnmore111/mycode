# Compaction & UI Context Viewer Analysis Report

## Executive Summary

The system has **partial support for compaction awareness** in the UI, but **pre-compaction context is NOT preserved** for viewing. The compaction event is yielded but not handled by the frontend, and only the compaction summary marker is tracked in the context viewer.

---

## 1. Backend: How Compaction Occurs

### 1.1 Compaction Entry Point (`opencode/session/prompt.py`, lines 246-254)

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

**Key Points:**
- Compaction is triggered when messages exceed 85% of context window (OVERFLOW_RATIO = 0.85)
- **The compact event is yielded with minimal data** - just session_id
- **Pre-compaction messages are NOT saved anywhere before replacement**
- The compacted result replaces the original messages list in-place

### 1.2 Compaction Process (`opencode/session/compaction.py`, lines 260-347)

```python
async def compact(messages, *, system, tools, model, api_key, api_base):
    # Step 1: prune tool outputs (in-place on original messages)
    messages, freed = prune_tool_outputs(messages)
    
    # Step 2: split into old / recent
    old, recent = _split_by_turns(messages, keep_turns=COMPACT_KEEP_TURNS)
    
    # Step 3: truncate tool outputs in old messages (DEEP COPY - original unchanged)
    truncated_old = _truncate_tool_outputs_for_summary(old)
    
    # Step 4: call LLM with same system prompt + tools
    summary_messages = list(truncated_old)
    summary_messages.append({"role": "user", "content": compaction_prompt})
    
    # Step 5: consume stream and collect summary text
    summary_text = ""
    async for event in llmmod.stream(stream_input):
        if isinstance(event, llmmod.TextDelta):
            summary_text += event.text
    
    # Step 6: extract summary
    summary = _extract_summary(summary_text)  # Strips <analysis>, keeps <summary>
    
    # Step 7: assemble result
    return _build_compact_result(summary, recent)
```

**Key Behaviors:**
- **Two-phase approach:** First tries pruning old tool outputs, only does summary if needed
- **Last N turns preserved verbatim** (COMPACT_KEEP_TURNS = 3 user turns)
- **Old messages summary is wrapped in a user message** with marker text:
  ```python
  "This session is being continued from a previous conversation that ran out of context. 
   The summary below covers the earlier portion..."
  ```
- **Result format:** `[user_summary_msg, *recent_turns]`
- **Original `old` messages are discarded** - NOT saved to DB or passed to frontend

---

## 2. Message Model & Storage

### 2.1 System Message Type (`opencode/session/message.py`, lines 121-137)

```python
@dataclass
class SystemMessage:
    """System-level message for internal state tracking.
    
    Subtypes:
    - info:             Informational (e.g. model switch notification)
    - warning:          Warning (e.g. approaching token limit)
    - error:            Error (e.g. API failure)
    - compact_boundary: Marks where compaction occurred (messages after this are kept)
    - local_command:    Local command output (e.g. /compact result) — filtered from API
    """
    id: str = ""
    session_id: str = ""
    role: Literal["system"] = "system"
    subtype: Literal["info", "warning", "error", "compact_boundary", "local_command"] = "info"
    content: str = ""
    time_created: int = 0
```

**Important:** The `compact_boundary` subtype is **defined but NOT used** in the codebase:
- No code creates a `compact_boundary` message after compaction
- The boundary is only used in context viewer heuristics (see section 3.1)

### 2.2 Message Persistence (`opencode/session/message.py`, lines 261-295)

```python
def save_message(msg: MessageInfo) -> None:
    """Persist a UserMessage or AssistantMessage to the database."""
    # Only saves role, time_created, and message-specific fields
    # SystemMessage instances are NOT persisted
```

**Current Status:**
- ✅ User messages saved
- ✅ Assistant messages + all parts saved
- ❌ System messages (including compaction markers) NOT persisted
- ❌ Compaction event not stored anywhere

---

## 3. Frontend: Context Viewer UI

### 3.1 Type Definitions (`web/src/types/index.ts`)

```typescript
export interface ContextSnapshot {
  // ... system, tools, messages ...
  compaction: {
    has_boundary: boolean
    boundary_index: number | null  // Index where compaction occurred
  }
  // ...
}

export interface ContextMessageInfo {
  index: number
  role: string
  content?: string
  cache_status: 'cached' | 'new'
  estimated_tokens: number
  is_compaction_summary?: boolean      // ← Marks compaction summary
  is_system_reminder?: boolean
  tool_calls?: Array<{ id: string; tool: string; args_preview: string }>
  content_truncated?: boolean
  full_length?: number
}
```

### 3.2 Context Snapshot Builder (`opencode/session/context.py`)

Detects compaction by searching for the marker text:

```python
# Line 20
_COMPACTION_MARKER = "continued from a previous conversation"

# Lines 156-158
if role == "user" and _COMPACTION_MARKER in content.lower():
    info["is_compaction_summary"] = True
    compaction_boundary_index = idx
```

**What gets included in snapshot:**
- ✅ System prompt (always cached)
- ✅ Tools list (always cached)
- ✅ All current messages with cache status (before = cached, last = new)
- ✅ Compaction summary marker detection (if present in current messages)
- ✅ Token estimates (heuristic only, not from API)
- ❌ **NO information about what messages were discarded during compaction**
- ❌ **NO historical data about compaction events**

### 3.3 ContextViewer Component (`web/src/components/ContextViewer.tsx`)

Displays the snapshot and marks compaction summaries:

```typescript
// Line 94
if (msg.is_compaction_summary) 
  badges.push(<span key="c" className="...">压缩摘要</span>)
```

**Renders:**
- ✅ System prompt with token count
- ✅ Tools list with token count
- ✅ All messages with:
  - Role icon (👤 user, 🤖 assistant, 🔧 tool)
  - Cache status (cached/new)
  - Token estimate
  - Badge if it's a compaction summary
  - Expandable content preview
- ❌ **NO UI to view pre-compaction context or discarded messages**
- ❌ **NO timeline/history of compaction events**

### 3.4 Event Handler (`web/src/hooks/useChat.ts`)

```typescript
// Lines 106-108
case 'context_snapshot':
  setContextSnapshot(event.data as unknown as ContextSnapshot)
  break
```

**Status:**
- ✅ `context_snapshot` event is handled
- ❌ **`compact` event is NOT handled** (line 107 shows it's defined in SSEEventType but never used)
- ❌ No UI feedback when compaction occurs

---

## 4. What's Missing: Data Preservation Gap

### 4.1 Pre-Compaction Context is Lost

| When Compaction Happens | What Exists | What's Lost |
|------------------------|-----------|-----------:|
| **Before compact() call** | `old_messages` in memory | The `old` messages split (lines 288) |
| **During compact() call** | `truncated_old` deep copy | Never returned from function |
| **After compact() call** | `[summary_user_msg, *recent]` | ALL messages before the summary |
| **At database** | Current messages only | Entire pre-compaction history |
| **At UI** | Compaction marker badge | Timeline of what was discarded |

### 4.2 Why It's Not Saved

1. **No event data:** The `compact` event only sends `session_id`, not the old messages
2. **No system message creation:** No `compact_boundary` message is created to mark what was discarded
3. **No storage mechanism:** No table to store compaction events or snapshots
4. **No UI layer:** Frontend has no way to query or display historical compaction

---

## 5. Current Capabilities vs. Desired Capabilities

### ✅ Currently Working

- Compaction detects when to trigger (85% of context window)
- Pruning of old tool outputs works
- Recent N turns are preserved verbatim
- Summary is generated via LLM
- Summary is detected in context viewer
- Compaction summary is marked with a badge
- Estimated token tracking works

### ❌ Missing Features

1. **No Compaction History**
   - Can't see when compaction occurred
   - Can't access pre-compaction message count
   - No timeline of compression events

2. **No Pre-Compaction Data Preservation**
   - Old messages discarded immediately
   - No summary of what was removed
   - Can't review what the compaction decided to summarize

3. **No Granular Compaction Control**
   - Can't see which turns were "old" vs "kept"
   - Can't adjust `COMPACT_KEEP_TURNS` per-session
   - No UI to manually trigger compaction

4. **No Compaction Feedback**
   - Frontend ignores `compact` SSE event
   - User doesn't see notification when it happens
   - No token savings metrics displayed

---

## 6. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│              prompt.py: Main Agentic Loop                   │
│  Line 246: Check context_limit & should_compact()           │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
            ┌──────────────────────────────┐
            │ compaction.compact()          │
            │ • Prunes tool outputs         │
            │ • Splits by turns (3 kept)    │
            │ • Generates LLM summary       │
            │ • Returns [summary, *recent]  │
            └──────────────────────────────┘
                            │
                            ├─────────────────────────────┐
                            │                             │
                            ▼                             ▼
         ✅ New messages          ❌ Old messages
         list returned            discarded (lost)
             │                            
             ├──→ persist_turn()         
             │    (saved to DB)          
             │                           
             └──→ PromptEvent("compact")
                  data: {session_id}
                  
                  ❌ Event sent to frontend
                     but NOT handled by useChat.ts
                  
                  ├──→ context_snapshot event
                  │    (detects summary via marker)
                  │
                  └──→ ContextViewer component
                       displays summary badge
                       ❌ No pre-compaction data shown
```

---

## 7. Recommendations for Implementation

### Phase 1: Minimal (Visibility Only)
1. **Handle `compact` event in frontend** - Show toast notification
2. **Store compaction metrics** - Add to session metadata (pre_count, post_count, tokens_freed)
3. **Display compaction info** - Show in ChatHeader or as expandable section

### Phase 2: Medium (History Tracking)
1. **Create `CompactionEvent` model** - Store in database with snapshot of old messages count/tokens
2. **Create `compact_boundary` messages** - Mark boundaries for UI/debugging
3. **Build compaction timeline UI** - Show history of compression events

### Phase 3: Full (Inspection & Recovery)
1. **Archive old message summaries** - Store full old message list pre-compaction (optional, storage cost)
2. **Build compaction inspector** - UI to view what was discarded and why
3. **Implement selective rollback** - Option to recover pre-compaction context if needed

---

## 8. Code Locations Summary

| Component | File | Key Lines | Status |
|-----------|------|-----------|--------|
| Compact trigger | `prompt.py` | 246-254 | ✅ Working |
| Compact algorithm | `compaction.py` | 260-347 | ✅ Working |
| Event emission | `prompt.py` | 254 | ✅ Sent |
| Frontend event handler | `useChat.ts` | 106-108 | ❌ Stub only |
| Context detection | `context.py` | 156-158 | ✅ Working |
| Type definitions | `types/index.ts` | 107, 147-150 | ✅ Defined |
| UI component | `ContextViewer.tsx` | 94 | ✅ Badge shown |
| Message model | `message.py` | 128-136 | ⚠️ Defined unused |
| DB persistence | `models.py` | - | ❌ No compaction table |

---

## Conclusion

**Current State:** The system performs compaction correctly but provides minimal UI visibility:
- ✅ Compaction works reliably
- ✅ Context snapshots are built
- ✅ Compaction summaries are detected and marked
- ❌ Pre-compaction data is completely lost
- ❌ No compaction timeline or history
- ❌ Frontend compaction event is not handled

**Key Gap:** There is no mechanism to view what was discarded during compaction or when/how often it occurs. The system silently compresses context and shows only the summary marker.
