# Issue #004: Agentic Loop 三层循环防护与执行优化

- **日期**: 2026-03-31
- **状态**: ✅ 已完成
- **影响范围**: `opencode/session/loop_guard.py`（新增）、`opencode/session/processor.py`、`opencode/session/prompt.py`、`opencode/cli/main.py`
- **参考**: PraisonAI doom loop detection、reivo-guard EWMA、Claude SDK agent loop、ToolCacheAgent 论文

---

## 1. 改造前的问题

原有的循环防护机制非常简单：

```python
DOOM_LOOP_THRESHOLD = 3
# 只有一种检测：相同 tool + 相同 input 重复 N 次
if all(inp == current_input for inp in last_inputs):
    doom_detected = True
```

**缺陷**：

| 问题 | 说明 |
|---|---|
| 只检测精确重复 | 无法发现 A↔B 乒乓震荡、输出不变但持续调用（poll 无进展）等模式 |
| 无硬限制兜底 | `max_iterations` 在 `prompt.py` 中，但 processor 不知道还剩多少迭代 |
| 无临近限制智能 | 接近上限时不会提前判断"是否还有意义继续" |
| 无结果缓存 | 相同 `read("foo.py")` 调用多次全部走 IO |
| 失败不重试 | 瞬时网络超时直接标记失败 |
| 全部工具并行 | 读取和写入工具混在一起并行，写操作可能冲突 |
| 无步骤状态 | 无法知道每步做了什么、花了多久、是否有进展 |

---

## 2. 架构设计

### 2.1 三层循环防护（`LoopGuard`）

参考 PraisonAI（三种检测器）、reivo-guard（滑动窗口+哈希匹配）、Claude SDK（硬限制）的实践，构建三层递进式防护：

```
Layer 1: Hard Limit Guard (绝对上限，不可突破)
    │
    ├── iteration >= max_iterations → FORCE_STOP
    └── iteration >= 80% max → WARN
    
Layer 2: Pattern Guard (模式检测)
    │
    ├── generic_repeat: 同 tool+input 重复 N 次 → STOP
    ├── ping_pong: A↔B 交替 N 次 → STOP
    └── stall: 同 tool+input+output 不变 N 次 → STOP
    
Layer 3: Near-Limit Intelligence (临近限制智能终止)
    │
    ├── 90%+ 且 N 轮无文本输出 → STOP
    └── 90%+ 且连续 3 次工具错误 → STOP
```

**执行顺序**：Layer 1 → Layer 2 → Layer 3，取最严格的 verdict。

### 2.2 单步原子化（`StepState`）

每个迭代作为一个原子步骤，记录：

```python
@dataclass
class StepState:
    iteration: int
    status: StepStatus          # PENDING → RUNNING → COMPLETED/FAILED
    tool_calls: list[dict]      # 本步调用了哪些工具
    text_produced: bool         # 是否产生了文本
    text_length: int            # 文本长度
    duration: float             # 耗时
    retry_count: int            # 重试次数
    cached_calls: int           # 缓存命中次数
```

通过 `guard.checkpoint` 导出完整状态快照，支持断点分析。

### 2.3 工具结果缓存（`ToolResultCache`）

参考 ToolCacheAgent 论文的思路，实现内容寻址缓存：

- **缓存键**: `SHA-256(tool_name + canonical_json(input))`
- **可缓存工具**: `read`, `glob`, `grep`, `listdir`, `webfetch`, `websearch`, `skill`（只读、确定性）
- **缓存失效**: 任何 `edit`/`write`/`bash` 执行成功后清空全部缓存
- **LRU 淘汰**: 超过 max_size 时清除最早 25%

### 2.4 读写分离并行调度

参考 Claude SDK 的策略：

```
读取工具 (read, glob, grep, listdir...)  → asyncio.gather 并行执行
写入工具 (edit, write, bash)              → 顺序执行（保证因果顺序）
```

### 2.5 失败重试

```python
def should_retry(tool_name, error, retry_count):
    if retry_count >= max_retries: return False
    if "validation" in error: return False      # 不重试验证错误
    if "permission" in error: return False       # 不重试权限错误
    # 重试瞬时错误
    return any(kw in error for kw in ["timeout", "connection", "rate limit", "429", "503"])
```

指数退避：`0.5s * (attempt + 1)`

---

## 3. 修改清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `opencode/session/loop_guard.py` | **新增** | 三层防护引擎 + 结果缓存 + 步骤状态（~350 行） |
| `opencode/session/processor.py` | **重写** | 集成缓存查找、读写分离、重试逻辑、步骤记录 |
| `opencode/session/prompt.py` | **重写** | 集成 LoopGuard、每步前检查 verdict、步骤原子化 |
| `opencode/cli/main.py` | **修改** | 处理 `guard_warn` / `guard_stop` 事件显示 |
| `tests/test_loop_guard.py` | **新增** | 21 个单元测试覆盖所有防护层和缓存逻辑 |

---

## 4. 配置参数

```python
@dataclass
class LoopGuardConfig:
    # Layer 1
    max_iterations: int = 50       # 绝对上限
    warn_at: float = 0.8           # 80% 时警告
    
    # Layer 2
    window_size: int = 20          # 模式检测滑动窗口
    repeat_threshold: int = 3      # 相同调用 3 次 → 停止
    ping_pong_threshold: int = 4   # A↔B 交替 4 次 → 停止
    stall_threshold: int = 5       # 相同结果 5 次 → 停止
    
    # Layer 3
    near_limit_ratio: float = 0.9  # 90% 开始智能判断
    empty_text_streak_limit: int = 3  # 3 轮无文本 → 停止
    
    # 缓存 & 重试
    cache_enabled: bool = True
    cache_max_size: int = 200
    max_retries: int = 2
```

---

## 5. 测试覆盖

| 测试类 | 测试数 | 覆盖 |
|---|---|---|
| `TestHardLimit` | 3 | 正常/警告/强停 |
| `TestPatternDetection` | 4 | 重复/无误报/乒乓/停滞 |
| `TestNearLimitIntelligence` | 2 | 无文本停止/有文本继续 |
| `TestToolResultCache` | 5 | 命中/未命中/不可缓存/淘汰/失效 |
| `TestStepState` | 2 | 生命周期/快照 |
| `TestRetryLogic` | 4 | 超时重试/验证不重试/超限不重试/限流重试 |
| `TestCacheInvalidationOnMutation` | 1 | 写操作触发缓存失效 |
| **总计** | **21** | |

---

## 6. 经验教训

1. **循环防护必须分层**：单一的"相同输入检测"只能捕获最简单的死循环。乒乓震荡、poll 无进展、接近上限但无产出等场景需要不同的检测策略。

2. **读写分离是并行执行的前提**：不分读写就全部并行，`edit` 后紧跟 `read` 可能读到旧内容。Claude SDK 的策略值得借鉴。

3. **结果缓存对 agent 效率影响显著**：agent 经常在多轮推理中重复读取相同文件。缓存命中可以将 IO 操作从 50-200ms 降到 0ms，同时减少发送给模型的冗余 token。

4. **写操作必须清缓存**：这是最容易遗忘的点。`edit("foo.py")` 后如果不清 `read("foo.py")` 的缓存，agent 会拿到旧内容。简单粗暴地清全部缓存是最安全的策略。

5. **步骤状态为可观测性打基础**：有了 `StepState`，可以很方便地实现"这轮做了什么"的可视化、性能分析、断点调试等功能。
