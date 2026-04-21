Launch sub-agent(s) to handle complex tasks with three execution modes:

**Modes:**

- `delegate` (default): Single sub-agent with enhanced context passing. Best for complex multi-step tasks that need full tool access (search, read, edit). Supports up to 30 turns.

- `parallel`: Multiple sub-agents running concurrently. Best for independent research tasks (searching multiple locations, fetching multiple URLs, analyzing multiple files). Only `explore` and `general` agents are allowed to prevent write conflicts.

- `isolated`: Sub-agent runs in a git worktree for safe, isolated file modifications. Changes are captured as a diff and can be auto-merged back. Best for experimental code changes, refactoring, or when multiple modification tasks need to run without interfering with each other.

**Agent types:**
- `general` — Full tool access (read + write), good for most tasks
- `explore` — Read-only, fast, specialized for code search and exploration
- `coder` — Write-focused, used in isolated mode for code modifications

**Usage guidelines:**
- Use `delegate` when a single task needs deep multi-step work
- Use `parallel` when you have 2+ independent research/search tasks
- Use `isolated` when file modifications need to be safe/reversible
- Provide `context` to give the sub-agent background information from the current conversation
- Set `auto_merge: true` in isolated mode to automatically apply changes
