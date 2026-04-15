# TODO

## 全局状态管理

### 已完成

- [x] `lsp/client.py` — `_MSG_ID` 竞态修复，改为 `itertools.count`
- [x] `provider/provider.py` — `list_providers()` 返回 `MappingProxyType` 只读视图，防止外部修改 `_state`
- [x] `agent/agent.py` — `get()` 返回 `copy.copy(agent)`，防止外部修改缓存
- [x] `config/models.py` — `Config` 添加 `frozen=True`，赋值直接报错
- [x] `session/prompt.py` — `_busy` 封装为 `_acquire_session()` / `_release_session()` / `is_session_busy()`
- [x] `tool/todo.py` — `_todos` 封装为 `get_todos()` / `set_todos()` / `clear_todos()`
- [x] `server/routes/session.py` — `_abort_signals` 封装为 `get_abort_signal()` / `set_abort_signal()` / `clear_abort_signal()`
- [x] 所有 mutation 函数（`invalidate` / `register` / `clear` / `close` 等）添加 `logger.debug` 追踪
- [x] **懒初始化加锁** — double-check locking 防止并发竞态
  - `provider/provider.py` — `asyncio.Lock`，提取 `_discover_providers()` 辅助函数
  - `agent/agent.py` — `threading.Lock`，提取 `_build_all_agents()` 辅助函数
  - `config/config.py` — `threading.Lock`，提取 `_load_and_merge()` 辅助函数
  - `storage/database.py` — `threading.Lock`，`get_engine()` 和 `get_session_factory()` 加锁
- [x] **`_todos` 内存泄漏** — `OrderedDict` + LRU 淘汰（上限 500 sessions），session 删除时 `clear_todos()`
- [x] **`_abort_signals` 内存泄漏** — SSE `event_generator()` 的 `finally` 块中调用 `clear_abort_signal()`

### 不需要做

- **AppContext + ContextVar 全局重构** — 当前项目是单进程单项目，不需要多实例隔离。所有全局状态已经通过 getter/setter 封装，如果将来需要多租户，getter/setter 就是天然切入点，把函数体从操作全局变量改为操作 `current_app().xxx` 即可，调用方零改动
