# Issue #005: prompt.py 缺少 ToolPart 导入导致 NameError

- **日期**: 2026-03-31
- **状态**: ✅ 已修复
- **提交**: `198c217`
- **影响范围**: `opencode/session/prompt.py`

---

## 1. 现象

用户执行查询后，agent 成功调用了 `listdir` 和 `webfetch` 工具（工具结果正常显示），但随后报错：

```
✓ listdir
  [dir]  agent-mem-note/
✓ Fetch https://opencode.ai
  - OpenCode | The open source AI coding agent...

✗ Error: name 'ToolPart' is not defined
```

## 2. 根因

在 Issue #004 改造 `prompt.py` 集成 loop_guard 步骤记录时，第 252 行新增了：

```python
for p in iteration_parts:
    if isinstance(p, ToolPart):  # ← 使用了 ToolPart
        step.tool_calls.append({...})
```

但 import 区域遗漏了 `ToolPart`：

```python
from opencode.session.message import (
    Part,
    TextPart,          # ← 有 TextPart
    # ToolPart 缺失！
    create_assistant_message,
    ...
)
```

## 3. 为什么第一轮工具能正常执行？

错误发生在 `process_stream` 的 `finish` 事件之后、步骤记录代码中。第一轮的 LLM 调用和工具执行不涉及 `ToolPart` 引用（processor.py 自己有正确的导入），只有在 `prompt.py` 尝试记录步骤状态时才触发 `NameError`。

## 4. 修复

```python
from opencode.session.message import (
    Part,
    TextPart,
    ToolPart,          # ← 补充导入
    create_assistant_message,
    ...
)
```

## 5. 经验教训

改造文件时如果新增了类型引用，必须同步检查 import 区域。这类错误静态分析（mypy/pyright）本可以在提交前捕获，应考虑在 CI 中启用类型检查。
