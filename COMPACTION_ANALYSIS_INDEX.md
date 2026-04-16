# Compaction Analysis - Documentation Index

## 📋 Overview

This analysis documents how compaction interacts with the UI and whether pre-compaction context is preserved in the OpenCode system.

**Short Answer:** Pre-compaction context is **NOT preserved**. It's discarded immediately after summarization.

---

## 📄 Analysis Documents

### 1. **COMPACTION_ANALYSIS.md** (Detailed Technical Report)
   **Best For:** Understanding the complete system architecture and gaps
   
   Contains:
   - Executive summary
   - Backend compaction algorithm details (lines 246-254 in prompt.py)
   - Message model and storage design
   - Frontend context viewer UI implementation
   - Data preservation gap analysis
   - Capabilities vs. missing features comparison
   - Data flow diagram
   - Implementation recommendations (3 phases)
   - Code location summary table

### 2. **COMPACTION_SUMMARY.txt** (Quick Reference Guide)
   **Best For:** Quick lookup and decision-making
   
   Contains:
   - What exists (✅ checkmarks)
   - What's missing (❌ X marks)
   - Data flow comparison with tool execution
   - Recommended fixes by time estimate
   - Key code locations table
   - Conclusion

### 3. **COMPACTION_CODE_TRACE.md** (Step-by-Step Code Walkthrough)
   **Best For:** Understanding the exact compaction process
   
   Contains:
   - 12 detailed steps with actual code snippets
   - Example scenarios with message layouts
   - Each phase explained (trigger → result)
   - Data loss points identified
   - Database persistence details
   - Complete summary table of where data is lost

### 4. **COMPACTION_FINAL_SUMMARY.txt** (Executive Summary)
   **Best For:** Understanding key findings and quick fixes
   
   Contains:
   - Key findings (what works, what's missing)
   - Data preservation gap visualization
   - Quickest fix (15 minutes)
   - Recommended quick wins (1-2 hours)
   - Files to read in order
   - Direct answer to your original question

---

## 🎯 Quick Navigation

**If you want to:**

- **Understand the complete picture** → Start with **COMPACTION_FINAL_SUMMARY.txt**
- **See what works and what's missing** → Read **COMPACTION_SUMMARY.txt**
- **Understand the code flow** → Read **COMPACTION_CODE_TRACE.md**
- **Get full details for implementation** → Read **COMPACTION_ANALYSIS.md**

---

## ✨ Key Findings At a Glance

### ✅ What Works (5 things)
1. Compaction algorithm is excellent and reliable
2. Context snapshots are built correctly
3. Compaction summaries are detected and marked
4. System preserves last 3 user turns verbatim
5. Trigger mechanism (85% of context window) works perfectly

### ❌ What's Missing (6 things)
1. **Pre-compaction context NOT preserved** - old messages discarded immediately
2. **Frontend event handler missing** - "compact" event sent but ignored by useChat.ts
3. **No compaction metrics** - event only has `{session_id}`, no data about what was removed
4. **No compaction history** - can't see when/why compaction happened
5. **No database storage** - no CompactionEvent model to persist compaction records
6. **No system message persistence** - SystemMessage.compact_boundary defined but never used

---

## 🔍 Critical Code Locations

| What | File | Lines | Status |
|------|------|-------|--------|
| Compaction algorithm | `opencode/session/compaction.py` | 260-347 | ✅ Working |
| Trigger check | `opencode/session/prompt.py` | 246-254 | ✅ Working |
| Event emission | `opencode/session/prompt.py` | 254 | ✅ Sent but no data |
| Summary detection | `opencode/session/context.py` | 156-158 | ✅ Working |
| **Frontend handler** | `web/src/hooks/useChat.ts` | 106-108 | ❌ **MISSING** |
| Event type defined | `web/src/types/index.ts` | 107 | ✅ Defined |
| UI badge display | `web/src/components/ContextViewer.tsx` | 94 | ✅ Shown |
| Message model | `opencode/session/message.py` | 128-136 | ⚠️ Defined but unused |
| Database models | `opencode/storage/models.py` | - | ❌ No compaction table |

---

## 🚀 Implementation Path

### Phase 1: Minimal (15-30 minutes)
- Add compact event handler in useChat.ts
- Show toast notification when compaction occurs
- No backend changes needed

### Phase 2: Short-term (1-2 hours)
- Modify compact() to return metrics (old_count, new_count, tokens_freed)
- Update event payload with data
- Show stats in ChatHeader: "Compressed: 100→45 messages (32K tokens)"

### Phase 3: Medium-term (4-6 hours)
- Create CompactionEvent database model
- Store compaction records
- Build compaction timeline UI

### Phase 4: Long-term (1-2 days)
- Archive pre-compaction messages (optional)
- Add "View Discarded Messages" UI
- Implement manual compaction trigger

---

## 📊 Data Preservation Gap Diagram

```
Before Compaction:
  [100 messages, 87K tokens] ──→ Triggers compaction (>85K)

During Compaction:
  Split: old=[msgs 0-96], recent=[msgs 97-99]
    ↓
  Summarize old via LLM
    ↓
  Build result: [summary_msg, *3 recent]

After Compaction:
  ❌ Old messages discarded (lost forever)
  ✅ Summary saved as user message
  ❌ Metrics not sent to frontend
  ❌ Frontend ignores "compact" event anyway
  ❌ No compaction record in database
  ❌ No way to see what was discarded
```

---

## 💡 Why This Matters

The system silently compresses context to keep the LLM model working efficiently. However:

- **User gets no feedback** when compaction happens
- **Can't inspect what was removed** for debugging
- **No history of compressions** across session
- **Can't recover** pre-compaction messages

This is by design for efficiency, but it makes debugging and inspection difficult.

---

## 📝 Related Code Files (Not Analyzed Here)

These files implement compaction support but aren't the focus:
- `opencode/session/llm.py` - LLM streaming and API calls
- `opencode/session/processor.py` - Main processing loop
- `web/src/api/stream.ts` - Frontend SSE streaming
- `opencode/tool/registry.py` - Tool definitions sent to LLM

---

## ❓ FAQ

**Q: Will pre-compaction messages break the system if I don't preserve them?**
A: No. The system is designed to discard them. Preserving them is optional for inspection/debugging.

**Q: Can I manually trigger compaction?**
A: Currently no. Only automatic trigger at 85% context window exists.

**Q: Where is the compaction summary stored?**
A: As a regular user message in the message history (prefixed with marker text).

**Q: How many turns are preserved?**
A: 3 user turns (COMPACT_KEEP_TURNS constant) plus all their associated assistant/tool messages.

**Q: What if pruning old tool outputs frees enough space?**
A: No full compaction occurs. Early return at ~20K tokens freed (PRUNE_MINIMUM).

**Q: Can I adjust compaction parameters per-session?**
A: Currently no. All values are hardcoded constants in compaction.py.

---

## 🔗 Cross-References

- System Architecture: See `COMPACTION_ANALYSIS.md` § 6 (Data Flow Diagram)
- Code Changes Needed: See `COMPACTION_SUMMARY.txt` (Recommended Fixes)
- Implementation Details: See `COMPACTION_CODE_TRACE.md` (All 12 phases)
- Quick Fix: See `COMPACTION_FINAL_SUMMARY.txt` (Quickest Fix section)

---

## ✅ Analysis Complete

All analysis documents have been generated and saved to the project root:

- ✅ COMPACTION_ANALYSIS.md (1,200+ lines, comprehensive)
- ✅ COMPACTION_SUMMARY.txt (quick reference)
- ✅ COMPACTION_CODE_TRACE.md (detailed walkthrough)
- ✅ COMPACTION_FINAL_SUMMARY.txt (executive summary)
- ✅ COMPACTION_ANALYSIS_INDEX.md (this file)

**Next Steps:**
1. Read COMPACTION_FINAL_SUMMARY.txt for overview
2. Choose implementation phase based on your needs
3. Reference specific documents for code changes
4. See code locations table above for files to modify
