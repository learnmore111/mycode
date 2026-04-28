# DashScope `qwen3.6-plus` 显式缓存接入说明

**更新日期：** 2026-04-23  
**相关文件：** `mycode/session/llm.py`、`tests/test_llm_dashscope_cache.py`、`mycode.json`

---

## 背景

这次更新的目标，是让 `DashScope` 的 `qwen3.6-plus` 在 OpenAI-compatible 调用模式下，能够稳定返回显式缓存相关统计字段，而不是仅依赖不稳定的隐式缓存命中。

本次实现里，**真正写入 `cache_control` 的地方只有一处**，其余改动都是围绕这处显式缓存声明做的条件判断、请求通道切换和统计解析。

---

## 1. 显式 `cache_control` 加在了哪里

### 1.1 `mycode/session/llm.py` 的 `_dashscope_explicit_cache_content()`

这里是唯一真正构造显式缓存控制字段的地方：

- 将纯文本包装成 OpenAI-compatible content block
- 在 block 内添加：`"cache_control": {"type": "ephemeral"}`

语义上等价于：

- 文本内容仍然是原 system prompt
- 但通过 content block 的形式，显式告诉 DashScope 这一段可参与缓存

### 1.2 `mycode/session/llm.py` 的 `_build_messages()`

显式缓存控制不会对所有消息生效，而是**只在 system prompt 构造阶段按条件插入**。

具体逻辑：

- 先把 `stream_input.system` 拼成一个完整的 `system_content`
- 如果当前模型命中 `_should_use_dashscope_explicit_cache()`
- 则把原本的普通 system 消息：
  - `{"role": "system", "content": system_content}`
- 改为：
  - `{"role": "system", "content": _dashscope_explicit_cache_content(system_content)}`

也就是说，这次显式 `cache_control` **只加在 system 消息内容里**。

---

## 2. 哪些地方没有加显式 `cache_control`

为了避免误解，这里把未加的位置也记录下来：

- **历史消息**：没有加  
  `stream_input.messages` 仍然直接 `extend` 到最终消息列表
- **当前 user 消息**：没有单独包装 `cache_control`
- **tools 定义**：没有插入 `cache_control`
- **其他模型**：没有启用这套逻辑
- **`qwen3.6-27b`**：目前仅加入模型配置，**没有**接入显式缓存分支

因此，这次更新的范围是：

- **只对 `dashscope/qwen3.6-plus` 生效**
- **只对 system prompt 生效**

---

## 3. 哪些代码是配套改动，而不是直接加 control 字段

### 3.1 `_should_use_dashscope_explicit_cache()`

这是显式缓存的开关判断。

当前条件非常明确：

- `provider_id == "dashscope"`
- `model.id == "qwen3.6-plus"`

作用：把显式缓存限定在单一模型上，避免影响其它 provider/model 的原有行为。

### 3.2 `_dashscope_explicit_cache_response()`

这里**没有新增 `cache_control` 字段本身**，但它是这次改动能真正生效的关键配套。

原因是：

- 之前统一走 `litellm.acompletion()`
- 在这个链路下，`cache_control` content block 可能无法被完整保留下发到 DashScope

所以这里新增了一个专用分支：

- 对 `qwen3.6-plus` 改走 `openai.AsyncOpenAI`
- 直接调用 `client.chat.completions.create(...)`
- 保证带 `cache_control` 的 message block 能原样发送

### 3.3 `stream()` 中的请求分流

`stream()` 新增了条件分流：

- 命中显式缓存条件：走 `_dashscope_explicit_cache_response()`
- 其他情况：仍走原来的 `litellm.acompletion()`

这部分同样不是“加 control 字段”，而是确保“已经加上的 control 字段”不会在请求链路中丢失。

### 3.4 `_get_cache_read_tokens()` / `_get_cache_write_tokens()`

这两个函数也不是加显式控制，而是为这次更新补齐**缓存统计可见性**。

当前支持读取：

- `usage.prompt_tokens_details.cached_tokens`
- `usage.prompt_tokens_details.cache_creation_input_tokens`

对应意义：

- `cached_tokens`：本轮命中的缓存 token 数
- `cache_creation_input_tokens`：本轮新写入缓存的 token 数

---

## 4. 测试覆盖

### `tests/test_llm_dashscope_cache.py`

这次新增测试主要覆盖两类行为：

- **消息构造测试**
  - 验证 `qwen3.6-plus` 的 system prompt 会被包装成 content block
  - 验证 block 中确实包含 `cache_control: {"type": "ephemeral"}`
  - 验证其他模型仍保持原始普通 system 文本格式

- **usage 解析测试**
  - 验证 `cached_tokens` 能正确映射为 cache read
  - 验证 `cache_creation_input_tokens` 能正确映射为 cache write

---

## 5. 与模型配置相关的补充说明

### `mycode.json`

这次还顺带更新了本地模型配置：

- 新增 `qwen3.6-plus`
- 新增 `qwen3.6-27b`
- 默认模型切换为 `dashscope/qwen3.6-plus`

但需要注意：

- **模型配置变更不等于显式缓存接入**
- 真正的显式 `cache_control` 逻辑目前仍只在 `mycode/session/llm.py` 中，并且仅针对 `qwen3.6-plus`

---

## 6. 本次更新结论

可以把这次改动概括成一句话：

> 在 `qwen3.6-plus` 的 system prompt 构造处，显式加入 `cache_control` content block，并通过专用 DashScope 调用通道保证该字段不被中间层吞掉，同时补齐缓存读写 token 的统计解析。

如果后续要把同样能力扩展到 `qwen3.6-27b` 或其他 DashScope 模型，建议复用以下三层结构：

1. 显式缓存内容构造
2. 模型级开关判断
3. 保留 `cache_control` 的专用请求通道
