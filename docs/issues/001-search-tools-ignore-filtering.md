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
    opencode/tool/__pycache__/edit.cpython-314.pyc
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

**按理说 `rg` (ripgrep) 默认会读取 `.gitignore` 并跳过这些目录，为什么还是搜到了？**

#### 关键原因：`rg` 的 `.gitignore` 查找机制

`rg` 读取 `.gitignore` 的行为取决于以下条件：

1. **必须在 Git 仓库中运行**：`rg` 会查找 `.git/` 目录来判断是否是 Git 仓库。如果当前目录或父目录中没有 `.git/`，`rg` 就**不会读取 `.gitignore`**。

2. **`.gitignore` 文件的查找路径是相对于 `.git/` 所在目录**：`rg` 从 `cwd` 向上查找 `.git/`，然后使用该位置的 `.gitignore`。

3. **搜索路径 `"."` 与 `cwd` 的交互**：修复前 grep 工具的代码逻辑是：

   ```python
   cwd = os.path.join(base, path) if not os.path.isabs(path) else path
   cmd.append(".")  # 搜索当前目录
   exec_cwd = cwd   # 设置工作目录为 cwd
   ```

   如果 `params.path` 传入了子目录（如 `"opencode/tool"`），那么：
   - `cwd` = `/project/opencode/tool`
   - `rg` 的工作目录变成了子目录
   - `rg` 从 `/project/opencode/tool` 向上查找 `.git/`
   
   这在**大多数情况下**能正确找到根目录的 `.gitignore`，但存在以下边界场景：

   **场景 A — `cwd` 不在 Git 仓库内**：如果传入了绝对路径 `path` 且该路径不在 Git 仓库内，`rg` 无法找到 `.git/`，`.gitignore` 规则完全失效。

   **场景 B — 符号链接或挂载点**：如果 `.venv` 是一个符号链接指向仓库外的位置，`rg` 在跟随符号链接时可能不会应用 `.gitignore` 规则。

   **场景 C — `.gitignore` 规则的路径匹配**：`.gitignore` 中的 `.venv/` 是相对于仓库根目录的。如果 `rg` 的 `cwd` 不是仓库根目录，而 `.gitignore` 规则又依赖于相对路径，可能会出现匹配偏移。

4. **`--no-ignore` 或环境变量**：如果用户的环境中设置了 `RIPGREP_CONFIG_PATH` 指向的配置文件包含 `--no-ignore`，或者 `rg` 的全局配置中禁用了 `.gitignore`，也会导致失效。

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

### 2.3 总结：没有统一的排除配置

| 工具 | 搜索引擎 | 依赖的排除机制 | 失败原因 |
|---|---|---|---|
| **Glob** | `glob.glob()` | ❌ 无 | `glob.glob()` 不读 `.gitignore` |
| **Grep (rg)** | ripgrep | `.gitignore`（隐式） | 某些 `cwd` 场景下 `.gitignore` 失效 |
| **Grep (fallback)** | GNU `grep` | ❌ 无 | 没有传 `--exclude-dir` |
| **ripgrep.py files()** | `rg --files` | `.gitignore`（隐式） | 同 Grep (rg) |
| **ripgrep.py files() fallback** | `find` | 仅排除 `.git` | 只硬编码了一个 `.git` |
| **list_dir** | `os.scandir()` | 硬编码 `.git`/`.DS_Store` | 排除列表太少 |

**核心问题**：整个项目没有一个统一的"应忽略目录列表"，每个工具各自为政，且大部分工具**隐式依赖 `rg` 读取 `.gitignore`** 来排除，没有显式保底机制。

## 3. 修复方案

### 3.1 新增统一排除模块 `opencode/file/ignore.py`

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

1. **不要隐式依赖外部工具的"默认行为"**：`rg` 默认读 `.gitignore` 是个很好的特性，但不能作为唯一的排除手段。工具的 `cwd`、环境变量、是否在 Git 仓库内等因素都可能让这个行为失效。

2. **每个搜索路径都必须有显式排除**：包括 `rg`、`grep`、`find`、`glob.glob()` —— 每条路径都要独立保证排除逻辑。

3. **统一配置是必须的**：当多个工具需要相同的排除逻辑时，必须提取为统一的常量模块，避免各处硬编码不同的排除列表导致不一致。

4. **fallback 路径容易被遗忘**：`if rg` 分支通常会被仔细处理，但 `else`（fallback 到 `grep`/`find`）分支往往被忽视，成为安全漏洞。
