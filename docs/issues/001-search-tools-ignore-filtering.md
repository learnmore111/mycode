# Issue #001: 搜索工具未过滤 .venv / \_\_pycache\_\_ 等非项目目录

- **日期**: 2026-03-31
- **状态**: ✅ 已修复
- **提交**: `aae23e0` — `fix: 搜索工具添加统一的目录排除过滤`
- **影响范围**: `grep` / `glob` / `list_dir` / `ripgrep.py`

---

## 1. 现象

Agent 在执行搜索时，返回了不应出现的结果：

```
✓ Grep edit.*tool|tool.*edit
    ./.venv/lib/python3.14/site-packages/...

✓ Glob **/*edit*
    mycode/tool/__pycache__/edit.cpython-314.pyc
```

用户明显是想搜索项目源码中与 `edit` 相关的工具文件，却搜到了虚拟环境的第三方包和 `__pycache__` 编译缓存。

## 2. 根因分析

### 2.1 Glob 工具 → `__pycache__` 泄漏

**直接原因**: `glob_tool.py` 使用 Python 标准库 `glob.glob(pattern, root_dir=base, recursive=True)`。

`glob.glob()` 是纯文件系统遍历，**完全不读取 `.gitignore`**，所以 `.venv/`、`__pycache__/`、`node_modules/` 内的文件全部会被匹配返回。

修复前代码：

```python
matches = sorted(globmod.glob(pattern, root_dir=base, recursive=True))
# 直接返回，无任何过滤
```

### 2.2 Grep 工具 → `.venv` 泄漏

这个更值得深入分析。项目的 `.gitignore` 明确包含了 `.venv/`：

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
```

代码设计上，grep 工具分两条路径：

```python
rg = shutil.which("rg")
if rg:
    # 使用 ripgrep（rg 默认读 .gitignore，能自动排除 .venv 等）
    cmd = [rg, "-rn", "--no-heading", "-m", "100"]
    ...
else:
    # fallback：使用系统 grep
    cmd = ["grep", "-rn", "-m", "100", pattern, cwd]
```

**按理说 `rg` (ripgrep) 默认会读取 `.gitignore` 并跳过 `.venv`，搜索结果应该是干净的。但为什么实际搜到了 `.venv` 的内容？**

#### 🔑 实际根因：环境没有安装 ripgrep

经过排查，发现当前开发环境（macOS）**根本没有安装 `rg`**：

```bash
$ command -v rg
# (无输出)
$ command -v grep
/usr/bin/grep
grep (BSD grep, GNU compatible) 2.6.0-FreeBSD
```

所以 `shutil.which("rg")` 返回 `None`，**代码从未走过 `rg` 分支，每次都走的 fallback `grep` 分支**。

完整的因果链：

```
没装 rg → shutil.which("rg") 返回 None → 走 else fallback 分支
→ 使用系统 grep，而 fallback 代码没有任何 --exclude-dir 参数
→ grep 暴力递归搜索所有文件 → .venv/__pycache__ 的内容全部被搜到
```

#### 为什么 fallback `grep` 完全没有排除？

修复前的 fallback 代码：

```python
cmd = ["grep", "-rn", "-m", "100", pattern, cwd]
```

**问题一目了然**：系统 `grep` 不像 `rg` 那样有任何"智能"行为——它不会读取 `.gitignore`，也不会自动跳过任何目录。这条命令等于"暴力搜索 cwd 下的所有文件"，自然会搜到 `.venv`、`__pycache__`、`node_modules` 里的所有内容。

GNU `grep` 需要**显式传递** `--exclude-dir` 参数才能排除目录：

```bash
# 正确的做法
grep -rn --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=node_modules "pattern" .
```

但修复前的代码完全没有加任何 `--exclude-dir` 参数。

#### 即使有 rg，也需要显式排除作为保底

虽然本次问题的直接原因是没装 `rg` 导致走了 fallback，但 `rg` 的 `.gitignore` 自动读取也不是万能的，以下场景仍可能失效：

| 场景 | 原因 |
|---|---|
| `cwd` 不在 Git 仓库内（传入了仓库外的绝对路径） | `rg` 找不到 `.git/`，不读 `.gitignore` |
| `.venv` 是符号链接指向仓库外 | `rg` 跟随链接后脱离 Git 仓库上下文 |
| 环境变量 `RIPGREP_CONFIG_PATH` 设了 `--no-ignore` | 全局禁用了 `.gitignore` 读取 |
| `rg` 版本差异 | 不同版本对 `.gitignore` 的处理可能有差异 |

因此修复时也为 `rg` 分支添加了显式排除参数作为保底。

### 2.3 总结：没有统一的排除配置

| 工具 | 搜索引擎 | 依赖的排除机制 | 实际失败原因 |
|---|---|---|---|
| **Glob** | `glob.glob()` | ❌ 无 | `glob.glob()` 不读 `.gitignore` |
| **Grep** | ~~ripgrep~~ → 实际是 GNU `grep` | ❌ 无 | **没装 `rg`**，fallback `grep` 无 `--exclude-dir` |
| **ripgrep.py files()** | ~~`rg --files`~~ → 实际是 `find` | 仅排除 `.git` | **没装 `rg`**，`find` 只排除了一个 `.git` |
| **ripgrep.py search()** | ~~`rg`~~ → 实际是 `grep` | ❌ 无 | **没装 `rg`**，fallback `grep` 无排除 |
| **list_dir** | `os.scandir()` | 硬编码 `.git`/`.DS_Store` | 排除列表太少 |

**核心问题**：

1. **环境缺少 `rg`**：代码设计上依赖 `rg` 的 `.gitignore` 读取能力来实现排除，但目标环境没装 `rg`，导致全部走 fallback 路径
2. **fallback 路径无排除逻辑**：fallback 到 `grep`/`find` 时，没有添加任何 `--exclude-dir` 或 `-not -path` 参数
3. **没有统一的排除配置**：整个项目没有一个统一的"应忽略目录列表"，各工具各自为政

## 3. 修复方案

### 3.1 新增统一排除模块 `mycode/file/ignore.py`

定义了三个核心常量：

- **`IGNORED_DIRS`**: 40+ 个应忽略的目录名（`.venv`, `__pycache__`, `node_modules` 等）
- **`IGNORED_FILE_PATTERNS`**: 二进制/缓存文件模式（`*.pyc`, `*.so` 等）
- **`RG_EXCLUDE_GLOBS`**: 为 ripgrep `--glob` 参数生成的排除模式（`!.venv`, `!__pycache__` 等）

以及两个辅助函数：
- `should_ignore_path(path)`: 检查完整路径是否应被忽略
- `should_ignore_entry(name)`: 检查目录条目名是否应被忽略

### 3.2 各工具修复

| 工具 | 修改 |
|---|---|
| **glob_tool.py** | `glob.glob()` 之后用 `should_ignore_path()` 过滤结果 |
| **grep.py (rg)** | 显式添加 `--glob !.venv` 等排除参数，不再纯依赖 `.gitignore` |
| **grep.py (fallback)** | 添加 `--exclude-dir` 参数 |
| **ripgrep.py files() (rg)** | 显式添加 `--glob` 排除 |
| **ripgrep.py files() (fallback)** | 为 `find` 添加多个 `-not -path` 排除 |
| **ripgrep.py search()** | 同上 |
| **file.py list_dir** | 用 `should_ignore_entry()` 替代硬编码的 `.git`/`.DS_Store` |

### 3.3 设计原则

- **显式优于隐式**：不依赖 `rg` 隐式读取 `.gitignore`，而是显式传递排除参数作为保底
- **统一配置**：所有排除模式集中在 `ignore.py`，新增需要排除的目录只需改一处
- **双层过滤**：`rg` 既有显式排除参数，又有 `.gitignore`（如果可用），两层保底

## 4. 经验教训

1. **不要假设外部工具一定存在**：代码的核心排除逻辑依赖 `rg`，但目标环境可能根本没装 `rg`。当设计 "优选路径 + fallback 路径" 的代码时，**两条路径必须提供等价的功能保障**，而不是让 fallback 成为"残废模式"。

2. **fallback 路径容易被遗忘，也最容易出问题**：`if rg` 分支因为 `rg` 自带 `.gitignore` 支持，看起来"自然就对了"；而 `else` 分支（fallback 到 `grep`/`find`）需要手动补齐排除逻辑，很容易被开发者忽略——毕竟开发者自己的环境通常有 `rg`，测试时根本不会走到 fallback。

3. **不要隐式依赖外部工具的"默认行为"**：`rg` 默认读 `.gitignore` 是个好特性，但不能作为唯一的排除手段。工具的 `cwd`、环境变量、是否在 Git 仓库内等因素都可能让这个行为失效。显式排除参数应作为保底。

4. **每个搜索路径都必须有显式排除**：包括 `rg`、`grep`、`find`、`glob.glob()` —— 每条路径都要独立保证排除逻辑，不能指望某一层"帮忙"排除。

5. **统一配置是必须的**：当多个工具需要相同的排除逻辑时，必须提取为统一的常量模块，避免各处硬编码不同的排除列表导致不一致。

6. **考虑在启动时检测关键依赖**：对于像 `rg` 这样能显著影响功能质量的外部工具，可以在程序启动时检测并给出安装建议（如 `brew install ripgrep`），让用户意识到当前在使用降级模式。
