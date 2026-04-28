# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync --extra dev

# Run interactive CLI
uv run mycode run

# Run headless (single message)
uv run mycode run -p "your message"

# Start API server
uv run mycode serve --port 4096

# Start both backend + frontend dev servers (one command)
uv run mycode dev
# Custom ports:
uv run mycode dev --port 8080 --frontend-port 5173

# Run all tests
uv run pytest tests/ -v

# Run a single test file
uv run pytest tests/test_foo.py -v

# Run tests with coverage
uv run pytest tests/ --cov=mycode --cov-report=term-missing

# Lint
uv run ruff check mycode/

# Auto-fix lint issues
uv run ruff check mycode/ --fix

# Type check
uv run mypy mycode/
```

## Architecture

This is a Python AI coding agent framework (reimplementation of the TypeScript [OpenCode](https://github.com/anomalyco/opencode) architecture). It exposes three interfaces: an interactive CLI (Click + Rich), an HTTP API (FastAPI + SSE), and a headless mode.

### Agentic Loop

The core loop lives in `mycode/session/`:
- **`prompt.py`** — entry point: selects agent, builds system prompt, loads tools, injects memory, persists messages to SQLite
- **`processor.py`** — drives the LLM→Tool loop: streams from litellm, parses tool calls, executes tools (read-only tools run in parallel, mutating tools run sequentially based on capability declarations), checks permissions, detects doom loops
- **`loop_guard.py`** — three-layer infinite loop protection: hard limit, pattern detection, LLM-based intelligence
- **`compaction.py`** — context compression: token estimation → LLM summary when context window fills
- **`llm.py`** — litellm wrapper with streaming, token counting, and cost calculation

### Tools (`mycode/tool/`)

14 built-in tools, each declaring capabilities (`is_read_only`, `is_destructive`, `is_concurrency_safe`). This drives parallel vs. sequential execution in the processor. Tools are sorted by name for prompt cache stability. All file-path tools perform path safety validation to prevent directory traversal.

Read-only (run in parallel): `bash` (safe commands), `read`, `glob`, `grep`, `listdir`, `webfetch`, `websearch`
Mutating (run sequentially): `write`, `edit`, `task`, `skill`, `question`, `todo`, `batch`

### Agents (`mycode/agent/`)

7 built-in agents with different tool sets and system prompts: `build` (default, full permissions), `plan` (read-only), `general` (subtasks), `explore` (search-focused), plus `compaction`/`title`/`summary` for internal use.

### Memory (`mycode/session/memory/`)

Two-layer memory:
1. **Session memory** (`memory.py`) — JSONL rolling summary per session, refined by LLM
2. **Structured memdir** (`memdir.py`) — long-term memory files in four categories (user/feedback/project/reference) with frontmatter format and a `MEMORY.md` index; `retrieval.py` does keyword + LLM-assisted retrieval; `extractor.py` auto-extracts memories in background

### Infrastructure

| Module | Purpose |
|--------|---------|
| `provider/` | Auto-discovers 14+ AI providers from env vars; `transform.py` adjusts params per model type; routes through litellm |
| `config/` | JSONC parsing + Pydantic v2 models + multi-layer merge (global → env → project → `.mycode`) |
| `storage/` | SQLAlchemy + SQLite; 5 tables: Project, Session, Message, Part, Permission |
| `bus/` | asyncio pub/sub event bus; 17 event types; supports typed, wildcard, and broadcast subscriptions |
| `permission/` | Wildcard rule evaluation; ask/reply blocking flow (allow/deny/ask) integrated into processor |
| `server/` | FastAPI app with 8 route modules and 26 endpoints; SSE for streaming messages and global events |
| `snapshot/` | Shadow git repo for track/diff/patch/restore |
| `lsp/` | JSON-RPC LSP client; 26 pre-defined language servers; auto-spawn + diagnostics |
| `mcp/` | MCP protocol client (stdio/HTTP); auto-reconnect up to 3 times |
| `plugin/` | Dynamic Python module loading; 7 hook types (before/after_tool, before/after_prompt, etc.) |

### Message Types

Three message types flow through the system:
- **UserMessage** — user input; `is_meta=True` hides from UI but sends to model; `origin` tracks source (human/api/cron/etc.)
- **AssistantMessage** — model output with token stats, cost, and timing
- **SystemMessage** — internal messages: `info`/`warning`/`error`/`compact_boundary`/`local_command`

`normalizeMessagesForAPI()` filters local_command messages and converts system messages before sending to LLM. `getMessagesAfterCompactBoundary()` truncates to the last compaction boundary.

## Configuration

Project config lives in `mycode.json` at the repo root (JSONC format). Custom providers/models require `limit.context` to be set manually for context bar and auto-compaction to work. Built-in providers auto-fetch limits from models.dev.

## Code Style

- Python 3.12+, line length 120, Ruff lint rules: E, F, W, I, N, UP, B, A, SIM, TCH
- Mypy strict mode
- pytest with `asyncio_mode = "auto"` (no need to mark async tests)
