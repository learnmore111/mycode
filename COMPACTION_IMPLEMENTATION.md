# Compaction Feature Implementation Guide

## Overview

This document describes the complete implementation of context compaction metrics, event handling, and audit trail persistence across three phases.

## Phase 1: Add Compact Event Handler ✅ COMPLETE

### What was implemented:
- Added `compact` event handler in `web/src/hooks/useChat.ts`
- Frontend now receives and logs compaction events
- Structured for future UI notifications and metrics display

### Files changed:
- `web/src/hooks/useChat.ts` - Added compact event case

### Impact:
- Backend compaction events are no longer silently ignored
- Console logging enables debugging of compaction behavior
- Foundation for Phase 2 metrics integration

---

## Phase 2: Add Metrics to Compaction Events ✅ COMPLETE

### What was implemented:

#### Backend Changes:
1. **Enhanced Token Estimation** (`opencode/session/compaction.py`):
   - Fixed `estimate_tokens()` to use UTF-8 byte encoding (÷3)
   - Improved accuracy for multi-byte characters (Chinese, Japanese, Korean)
   - Changed from ASCII-based (÷4) to byte-based calculation

2. **CompactionMetrics NamedTuple** (`opencode/session/compaction.py`):
   ```python
   CompactionMetrics = namedtuple('CompactionMetrics', [
       'old_message_count',     # messages summarized
       'old_message_tokens',    # tokens freed
       'summary_length',        # summary size
       'removed_turn_count',    # user turns removed
   ])
   ```

3. **Return Tuple** (`opencode/session/compaction.py`):
   - `compact()` now returns `(messages, metrics)` instead of just `messages`
   - Enables caller to access detailed compaction information

4. **Event Payload** (`opencode/session/prompt.py`):
   - Compact event now includes all metrics
   - Event data structure:
     ```json
     {
       "session_id": "...",
       "old_message_count": 5,
       "old_message_tokens": 1200,
       "summary_length": 450,
       "removed_turn_count": 2
     }
     ```

#### Frontend Changes:
1. **Metrics Handler** (`web/src/hooks/useChat.ts`):
   - Logs compaction metrics to console
   - Structured type for metric data
   - Ready for UI notifications

#### Tests:
- Updated all compact() calls to unpack tuple
- Added `test_compact_returns_metrics()` - validates metrics structure
- Added `test_compact_metrics_zero_when_no_compaction()` - edge case

### Impact:
- UI can display what was lost during compaction
- Enables performance monitoring of compression efficiency
- Metrics available in real-time for each compaction event

---

## Phase 3: Persist Pre-Compaction Context ✅ COMPLETE

### What was implemented:

#### Database Schema:
1. **CompactionEventTable** (`opencode/storage/models.py`):
   ```sql
   CREATE TABLE compaction_event (
       id VARCHAR PRIMARY KEY,
       session_id VARCHAR NOT NULL (indexed),
       iteration INTEGER NOT NULL,
       old_message_count INTEGER NOT NULL,
       old_message_tokens INTEGER NOT NULL,
       summary_length INTEGER NOT NULL,
       removed_turn_count INTEGER NOT NULL,
       old_messages TEXT NOT NULL (JSON),
       summary TEXT NOT NULL,
       time_created INTEGER NOT NULL
   )
   ```

2. **Enhanced CompactionMetrics** (`opencode/session/compaction.py`):
   ```python
   CompactionMetrics = namedtuple('CompactionMetrics', [
       'old_message_count',
       'old_message_tokens',
       'summary_length',
       'removed_turn_count',
       'old_messages',    # ← NEW: original messages for audit trail
       'summary',         # ← NEW: generated summary text
   ])
   ```

#### Backend Persistence:
1. **save_compaction_event()** (`opencode/session/message.py`):
   - Persists compaction event with full context
   - Stores old messages as JSON array
   - Runs asynchronously (non-blocking)

2. **get_compaction_events()** (`opencode/session/message.py`):
   - Retrieves all compaction events for a session
   - Returns complete audit trail

3. **Prompt Integration** (`opencode/session/prompt.py`):
   - Unpacks metrics tuple: `messages, compact_metrics = await compaction.compact(...)`
   - Calls `save_compaction_event()` asynchronously after yielding event
   - Non-blocking: user sees event immediately

#### API Endpoint:
1. **GET /api/session/{session_id}/compaction-events** (`opencode/server/routes/session.py`):
   - Returns array of all compaction events for a session
   - Full metrics and old messages included

#### Frontend:
1. **API Client** (`web/src/api/compaction.ts`):
   ```typescript
   export async function getCompactionEvents(sessionId: string): Promise<CompactionEvent[]>
   ```

2. **TypeScript Types** (`web/src/types/index.ts`):
   ```typescript
   export interface CompactionEvent {
       id: string
       session_id: string
       iteration: number
       old_message_count: number
       old_message_tokens: number
       summary_length: number
       removed_turn_count: number
       old_messages: Array<{ role: string; content?: string }>
       summary: string
       time_created: number
   }
   ```

#### Tests:
- Added `test_compact_metrics_include_old_messages()` - validates persistence data

### Impact:
- Complete audit trail of all compressions
- Old messages can be recovered for review
- Users understand exactly what was removed
- Database persistence enables long-term analysis

---

## Architecture Overview

```
BACKEND FLOW:
┌─────────────────────────────────────────────────────────────┐
│ 1. prompt() checks if compaction needed                      │
│    - Calls compaction.compact(messages, **kwargs)           │
│    - Returns (compacted_messages, CompactionMetrics)        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Yield compact event to client                            │
│    - Event includes all metrics from CompactionMetrics      │
│    - Client receives immediately                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Async background: save_compaction_event()                │
│    - Persists metrics, old_messages, summary to database    │
│    - Non-blocking (doesn't delay user experience)           │
└─────────────────────────────────────────────────────────────┘

FRONTEND FLOW:
┌─────────────────────────────────────────────────────────────┐
│ SSE Event: type='compact', data={metrics}                   │
│ ↓                                                            │
│ useChat.ts: case 'compact' handler logs metrics             │
│ ↓                                                            │
│ Optional: UI displays compaction notification               │
│ ↓                                                            │
│ Optional: Fetch /api/session/{id}/compaction-events         │
│ ↓                                                            │
│ Display compaction history in UI                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Flow Example

### Before Compaction:
```
Messages in memory (100 KB):
[
  {"role": "user", "content": "..."},     // Turn 1
  {"role": "assistant", "content": "..."},
  {"role": "user", "content": "..."},     // Turn 2
  {"role": "assistant", "content": "..."},
  {"role": "user", "content": "..."},     // Turn 3 (kept)
  {"role": "assistant", "content": "..."},
  {"role": "user", "content": "..."},     // Turn 4 (kept)
  {"role": "assistant", "content": "..."},
]
```

### Compaction Occurs:
1. Split by turns: old=[Turn 1, Turn 2], recent=[Turn 3, Turn 4]
2. Summarize old messages via LLM
3. Return (new_messages, metrics)

### Metrics Generated:
```
CompactionMetrics(
  old_message_count=4,
  old_message_tokens=12500,
  summary_length=450,
  removed_turn_count=2,
  old_messages=[...],    # Full Turn 1 and 2
  summary="Summary text"
)
```

### Event Sent to Client:
```json
{
  "type": "compact",
  "data": {
    "session_id": "sess-abc123",
    "old_message_count": 4,
    "old_message_tokens": 12500,
    "summary_length": 450,
    "removed_turn_count": 2
  }
}
```

### Persisted in Database:
```
INSERT INTO compaction_event VALUES (
  id='comp-xyz789',
  session_id='sess-abc123',
  iteration=5,
  old_message_count=4,
  old_message_tokens=12500,
  summary_length=450,
  removed_turn_count=2,
  old_messages='[{"role":"user",...}...]',
  summary='Summary text',
  time_created=1713275400000
)
```

---

## Usage Examples

### Retrieving Compaction History:
```typescript
import { getCompactionEvents } from '@/api/compaction'

const events = await getCompactionEvents(sessionId)
events.forEach(event => {
  console.log(`Iteration ${event.iteration}:`)
  console.log(`  Removed ${event.removed_turn_count} turns`)
  console.log(`  Freed ${event.old_message_tokens} tokens`)
  console.log(`  Summary: ${event.summary.substring(0, 100)}...`)
})
```

### Creating UI for Compaction Timeline:
```typescript
// In a React component
const [compactionEvents, setCompactionEvents] = useState<CompactionEvent[]>([])

useEffect(() => {
  if (sessionId) {
    getCompactionEvents(sessionId).then(setCompactionEvents)
  }
}, [sessionId])

return (
  <div>
    {compactionEvents.map(event => (
      <div key={event.id}>
        <p>Iteration {event.iteration}: Removed {event.removed_turn_count} turns</p>
        <details>
          <summary>View summary</summary>
          <p>{event.summary}</p>
        </details>
      </div>
    ))}
  </div>
)
```

---

## Testing

### Run all tests:
```bash
# Backend tests (update to use pytest or your test runner)
python3 -m pytest tests/test_compaction.py -xvs
```

### Key test cases covered:
- `test_compact_returns_metrics` - Validates metrics tuple
- `test_compact_metrics_zero_when_no_compaction` - Edge case handling
- `test_compact_metrics_include_old_messages` - Audit trail capture
- All existing compaction tests (updated for tuple unpacking)

---

## Performance Considerations

### Memory:
- Old messages persisted in database (not kept in memory)
- CompactionMetrics tuple is lightweight (~100 bytes)
- Event payload minimal (~200 bytes)

### Database:
- Compaction events stored separately (not in messages table)
- Indexed by session_id for efficient retrieval
- old_messages stored as JSON (queryable if needed)

### Async Persistence:
- `save_compaction_event()` runs in background thread
- Zero impact on event streaming to client
- Database write happens after client receives event

---

## Future Enhancements

### Phase 4 (Optional):
- [ ] UI component to display compaction timeline
- [ ] Ability to expand old messages in ContextViewer
- [ ] Export compaction history as JSON
- [ ] Analytics on compression effectiveness
- [ ] Configurable compaction thresholds per model

### Phase 5 (Optional):
- [ ] Compression recovery (undo compaction if needed)
- [ ] Selective message preservation
- [ ] Compaction strategy optimization
- [ ] Integration with memory system

---

## Summary of Changes by File

### Core Compaction:
- `opencode/session/compaction.py` - Enhanced metrics, tuple return
- `opencode/session/prompt.py` - Event emission, async persistence

### Persistence:
- `opencode/storage/models.py` - CompactionEventTable schema
- `opencode/session/message.py` - save/get compaction event functions
- `opencode/server/routes/session.py` - API endpoint

### Frontend:
- `web/src/hooks/useChat.ts` - Compact event handler
- `web/src/api/compaction.ts` - API client function
- `web/src/types/index.ts` - CompactionEvent type

### Tests:
- `tests/test_compaction.py` - Updated for tuple, new metrics tests

---

## Deployment Notes

1. **Database Migration**: Run SQLAlchemy create_all() to create CompactionEventTable
2. **No API Changes**: New endpoint is additive only
3. **Backward Compatible**: Old code reading events still works
4. **Async Safety**: No blocking operations in critical path

