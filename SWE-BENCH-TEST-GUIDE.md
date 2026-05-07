# SWE-bench 测试使用说明

> 使用 **mycode** AI Code Agent 对 SWE-bench 高难度仓库进行自动化修复测试的完整指南

---

## 目录

1. [快速开始](#1-快速开始)
2. [环境准备](#2-环境准备)
3. [命令参考](#3-命令参考)
4. [完整工作流程](#4-完整工作流程)
5. [修复提示词模板](#5-修复提示词模板)
6. [常见问题](#6-常见问题)

---

## 1. 快速开始

### 一键启动（4 步）

```bash
# Step 1: 初始化 3 个高难度测试仓库
./run_swe_bench_test.sh setup

# Step 2: 确认问题存在（预期 FAIL）
./run_swe_bench_test.sh test django

# Step 3: 启动 mycode agent 修复（3 种方式可选）★

# ★ 方式 A: 前端 UI 模式（推荐，浏览器操作体验最好）
./run_swe_bench_test.sh dev django
# 自动打开 http://localhost:3000，在界面中输入提示词即可修复

# 方式 B: CLI 交互模式（终端内操作）
./run_swe_bench_test.sh fix django

# 方式 C: Headless 模式（自动运行，无交互）
./run_swe_bench_test.sh fix-h django "你的修复提示词"

# Step 4: 验证修复结果（期望 PASS）
./run_swe_bench_test.sh verify django
```

### 核心原理：为什么不能在目标仓库运行 mycode？

| 目录 | 内容 | 能否运行 mycode |
|------|------|----------------|
| `mycode/` | mycode 项目源码 + pyproject.toml | **能** ✅ |
| `django-test/` | Django 源码 + Django 的 pyproject.toml | **不能** ❌ |

**mycode 的 `run` 命令支持指定目标目录**（无需进入目录）：

```bash
cd /Users/lihuijin/Desktop/code-agent/mycode   # 在 mycode 项目目录
uv run mycode run ~/swe-bench-test/django-test # 指定目标仓库作为工作目录
uv run mycode run ~/swe-bench-test/django-test -p "提示词"  # headless 模式
```

# Step 4: 验证修复结果（期望 PASS）
./run_swe_bench_test.sh verify django
```

### 查看状态

```bash
./run_swe_bench_test.sh status
```

---

## 2. 环境准备

### 前置依赖

| 工具 | 用途 | 安装 |
|------|------|------|
| **uv** | Python 包管理器（替代 pip） | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **git** | 版本控制 / 回退 | macOS 自带 |
| **pytest** | 运行测试验证 | 通过 uv 安装 |

### 检查 uv 是否安装

```bash
uv --version
# 应输出: uv 0.x.x
```

如未安装：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# 重启终端后生效
source ~/.zshrc   # 或 ~/.bashrc
```

### GitHub 加速镜像（国内必配）

```bash
# 查看当前镜像
./run_swe_bench_test.sh mirror

# 切换加速源
./run_swe_bench_test.sh mirror ghfast      # 推荐
./run_swe_bench_test.sh mirror kkgithub    # 备选
./run_swe_bench_test.sh mirror bgithub     # 备选
./run_swe_bench_test.sh mirror origin      # 官方源（可能很慢）
```

可选：设置 Git 全局代理（一劳永逸）

```bash
git config --global url."https://ghfast.top/".insteadOf https://

# 取消时：
git config --global --unset url."https://ghfast.top/".insteadOf
```

---

## 3. 命令参考

### 所有命令一览

| 命令 | 说明 | 示例 |
|------|------|------|
| **`setup`** | 克隆仓库 + 创建 venv + 安装依赖 | `./run_swe_bench_test.sh setup` |
| **`dev <repo>`** | **启动前端 UI 模式修复（推荐体验）** | `./run_swe_bench_test.sh dev django` ★ |
| **`fix <repo>`** | 启动 mycode CLI 交互模式修复 | `./run_swe_bench_test.sh fix django` |
| **`fix-h <repo> [msg]`** | 启动 mycode headless 模式修复 | `./run_swe_bench_test.sh fix-h django` |
| **`reset <repo>`** | Git 回退到干净状态 | `./run_swe_bench_test.sh reset django` |
| **`test <repo>`** | 运行失败测试（确认问题存在） | `./run_swe_bench_test.sh test sklearn` |
| **`verify <repo>`** | 验证修复是否成功 | `./run_swe_bench_test.sh verify sympy` |
| **`diff <repo>`** | 查看 agent 的代码修改 | `./run_swe_bench_test.sh diff django` |
| **`mirror [name]`** | 查看/切换 GitHub 加速源 | `./run_swe_bench_test.sh mirror ghfast` |
| **`status`** | 显示所有仓库状态概览 | `./run_swe_bench_test.sh status` |

### 详细说明

#### `setup` — 初始化环境

```bash
# 默认使用 ghfast 加速克隆
./run_swe_bench_test.sh setup

# 指定其他加速源
GITHUB_MIRROR=kkgithub ./run_swe_bench_test.sh setup

# 使用官方源（不加速）
GITHUB_MIRROR=origin ./run_swe_bench_test.sh setup
```

**内部执行过程：**
1. 克隆 3 个仓库到 `~/swe-bench-test/`
2. 为每个仓库用 `uv venv` 创建 `.venv`
3. 用 `uv pip install -e .` 安装项目及其开发依赖

#### `reset <repo>` — 回退重置

```bash
./run_swe_bench_test.sh reset django
# 输出:
# 🔄 回退仓库: django
# 当前 commit: abc1234
# 已修改文件: 3 个
# 未跟踪文件: 1 个
# ✅ 回退完成!
```

**回退操作包含：**
- `git stash` — 暂存当前修改
- `git checkout -- .` — 丢弃工作区修改
- `git clean -fd` — 清理未跟踪的新文件

#### `test <repo>` — 确认问题存在

```bash
./run_swe_bench_test.sh test sympy
# 输出:
# 实例 ID: sympy__sympy-11111
# 失败测试: sympy/core/tests/test_sympify.py::test_sympify_attribute_error
# 问题描述: ...
#
# 运行测试 (预期应该 FAIL)...
# ... FAILED ...
#
# ✅ 确认测试失败 (这是预期的，说明问题存在)
```

#### `verify <repo>` — 验证修复

```bash
./run_swe_bench_test.sh verify django
# 成功时:
# 🎉 测试通过! 修复成功!
# 补丁已保存: ~/swe-bench-test/fixes/django_20260428_103600.patch

# 失败时:
# ❌ 测试失败，修复不完整，继续调试
# 建议: 重新启动 mycode agent 补充修复
```

#### `diff <repo>` — 查看修改内容

```bash
./run_swe_bench_test.sh diff django
# 显示:
# 已修改的文件:
# django/core/handlers/base.py
#
# 详细 diff:
# -------------------------------------------
# diff --git a/django/core/handlers/base.py b/django/core/handlers/base.py
# @@ -150,7 +150,7 @@
# -        for middleware_method in self._request_middleware:
# +        for middleware_method in reversed(self._request_middleware):
```

---

## 4. 完整工作流程

### 标准测试循环（推荐）

```
┌──────────┐    ┌──────────┐    ┌──────────────┐    ┌───────────┐
│  setup   │ -> │   test   │ -> │ mycode fix   │ -> │  verify   │
│ (初始化) │    │(确认FAIL)│    │ (修复代码)   │    │(检查PASS) │
└──────────┘    └──────────┘    └──────┬───────┘    └───────────┘
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
               ┌─────────┐ ┌────────┐ ┌─────────┐
               │ dev(UI) │ │fix(CLI) │ │fix-h(auto)│
               │ ★推荐  │ │        │ │         │
               └─────────┘ └────────┘ └─────────┘

                      ^                                |
                      |          失败                   |
                      +----------<---------------------+
                                 |
                            reset (重来)
```

### 具体操作步骤

#### 第一次使用

```bash
# 1. 初始化全部仓库（只需一次）
./run_swe_bench_test.sh setup

# 2. 选择一个仓库开始测试，例如 Django
./run_swe_bench_test.sh test django
```

#### 启动 mycode agent 进行修复（3 种方式）

**方式 A：前端 UI 模式（推荐，体验最佳）** ⭐

```bash
# 一键启动 — 自动打开浏览器，在界面中操作
./run_swe_bench_test.sh dev django

# 等价于手动执行:
cd /Users/lihuijin/Desktop/code-agent/mycode
uv run mycode dev --directory ~/swe-bench-test/django-test
```

> 启动后：
> - **后端 API**: http://127.0.0.1:4096
> - **前端 UI**: http://localhost:3000（自动打开浏览器）
> - 在聊天界面中粘贴**修复提示词**即可开始，可直观看到 agent 的每一步操作
> - 支持：实时流式输出、文件浏览、Git diff 查看、代码回滚

**方式 B：CLI 交互模式（终端内操作）**

```bash
./run_swe_bench_test.sh fix django
```

#### 验证并循环

```bash
# 验证修复结果
./run_swe_bench_test.sh verify django

# 如果失败 → 回退重来
./run_swe_bench_test.sh reset django

# 再次测试 → 再次修复 → 再验证...
./run_swe_bench_test.sh test django
# (重复上述循环)
```

### 批量测试多个仓库

```bash
# 同时测试所有 3 个仓库的问题
for repo in django sklearn sympy; do
  echo "=== Testing $repo ==="
  ./run_swe_bench_test.sh test "$repo"
done
```

---

## 5. 修复提示词模板

### 通用结构

```
你是一个软件工程专家。请根据以下 GitHub Issue 描述修复代码库中的问题。

【问题描述】
{problem_statement}

【相关文件】
- 文件路径 1
- 文件路径 2

【失败测试】
- {FAIL_TO_PASS test_name}

【要求】
1. 分析问题根因
2. 找出需要修改的代码位置
3. 生成最小化的修复补丁
4. 确保修复后相关测试能够通过

请开始分析和修复。
```

### Django — 中间件执行顺序问题

**实例 ID**: `django__django-11000`
**核心文件**: `django/core/handlers/base.py`
**失败测试**: `tests/handlers/test_base.py::TestMiddlewareOrder::test_lifo_order`

```bash
./run_swe_bench_test.sh fix-h django "
你是一个 Django 核心开发者，熟悉 Django 的请求处理中间件机制。

【Issue 描述】
django/core/handlers/base.py 中的 BaseHandler.get_response() 方法，
中间件执行顺序有误。当前按注册顺序执行 request_middleware，
但根据 RFC 规范和实际需求，应按照 LIFO（后进先出）顺序执行，
即最后注册的中间件最先处理请求。

【相关代码位置】
请查看 django/core/handlers/base.py 文件的 get_response() 或 _get_response() 方法，
找到 for middleware_method in self._request_middleware 这一行循环。

【失败的测试用例】
tests/handlers/test_base.py::TestMiddlewareOrder::test_lifo_order

【任务要求】
1. 先阅读 django/core/handlers/base.py 了解当前实现
2. 分析 LIFO 顺序对中间件行为的影响
3. 修改循环为 reversed() 顺序（或其他正确的实现方式）
4. 运行 pytest tests/handlers/test_base.py::TestMiddlewareOrder -xv 验证修复
"
```

### scikit-learn — predict 返回类型问题

**实例 ID**: `sklearn__sklearn-22000`
**核心文件**: `sklearn/ensemble/_forest.py`
**失败测试**: `sklearn/ensemble/tests/test_forest.py::test_predict_return_type`

```bash
./run_swe_bench_test.sh fix-h sklearn "
你是一个机器学习框架开发者，熟悉 scikit-learn 的 API 设计规范。

【Issue 描述】
RandomForestClassifier.predict() 方法在某些边界情况下返回值类型不一致。
当输入数据为特定形状或类别数较少时，predict 可能返回概率数组而非整数类别标签。
这违反了 sklearn 的 API 约定：predict 必须返回与输入样本数相同的类别标签数组。

【相关代码位置】
sklearn/ensemble/_forest.py 中的 RandomForestClassifier.predict() 方法

【失败的测试用例】
sklearn/ensemble/tests/test_forest.py::test_predict_return_type

【任务要求】
1. 阅读 RandomForestClassifier 类的 predict 和 predict_proba 方法实现
2. 找出 predict 返回非标签值的边界条件
3. 确保 predict 最终返回 np.argmax(proba, axis=1) 映射到 self.classes_
4. 运行测试验证修复
"
```

### SymPy — sympify 异常处理缺失

**实例 ID**: `sympy__sympy-11111`
**核心文件**: `sympy/core/sympify.py`
**失败测试**: `sympy/core/tests/test_sympify.py::test_sympify_attribute_error`

```bash
./run_swe_bench_test.sh fix-h sympy "
你是一个符号数学计算专家，熟悉 Python 的异常处理和 SymPy 的对象转换机制。

【Issue 描述】
sympy.core.sympify.sympify() 函数用于将任意 Python 对象转换为 SymPy 表达式。
当前实现在 except 子句中只捕获了 SympifyError、OverflowError 和 ValueError，
但没有捕获 AttributeError。
当传入的对象缺少某些属性访问时会抛出 AttributeError 导致程序崩溃，
而不是优雅地降级或报错。

【相关代码位置】
sympy/core/sympify.py 第 508 行附近的 try/except 块

【失败的测试用例】
sympy/core/tests/test_sympify.py::test_sympify_attribute_error

【任务要求】
1. 阅读 sympify 函数的实现，理解其异常处理逻辑
2. 在 except 子句中添加 AttributeError 到捕获列表
3. 确保添加后不影响现有功能
4. 运行测试验证修复
"
```

---

## 6. 常见问题

### Q1: setup 时 git clone 超时或太慢？

```bash
# 切换到更快的加速源
./run_swe_bench_test.sh mirror kkgithub
./run_swe_bench_test.sh setup

# 或者手动配置全局代理
git config --global url."https://ghfast.top/".insteadOf https://
```

### Q2: uv pip install 报错找不到编译器（C/C++ extension）

scikit-learn 包含 C/C++ 扩展，需要编译工具链：

```bash
# macOS
xcode-select --install

# Ubuntu/Debian
sudo apt-get install build-essential python3-dev

# 如果仍有问题，尝试预编译 wheel（uv 会自动选择最优方案）
uv pip install --python .venv/bin/python scikit-learn
```

### Q3: 如何只测试一个仓库而不初始化全部？

脚本已经支持单独操作每个仓库：

```bash
# 只初始化 Django（需手动 clone 后再运行部分命令）
cd ~/swe-bench-test
git clone --depth 1 https://ghfast.top/https://github.com/django/django.git django-test
cd django-test && uv venv .venv && uv pip install -e ".[dev]" --python .venv/bin/python

# 然后正常使用 test/reset/verify
./run_swe_bench_test.sh test django
```

### Q4: 如何使用前端 UI 界面进行修复？（推荐）

```bash
# 一键启动前端 UI 模式
./run_swe_bench_test.sh dev django

# 启动后浏览器会自动打开 http://localhost:3000
# 在聊天界面中粘贴修复提示词即可
```

**UI 模式的优势**：
- 可视化操作：实时看到 agent 的每一步（文件读取、搜索、编辑）
- 文件浏览器：直接在界面中查看仓库文件结构
- Git 集成：自动显示 diff、支持一键回滚到任意步骤
- 流式输出：像 ChatGPT 一样逐字看到 agent 的思考和回复
- 多会话：可以同时开启多个测试任务，侧边栏切换

### Q5: 为什么不能在目标仓库目录运行 mycode？

**原因**：`uv run` 会读取当前目录的 `pyproject.toml` 来确定运行哪个项目。
- `mycode/pyproject.toml` → 定义了 `mycode` 命令 ✅
- `django-test/pyproject.toml` → 是 Django 的配置，没有 mycode ❌

**正确做法**：从 mycode 项目目录启动，通过参数指定目标仓库：

```bash
# 推荐：使用脚本自动处理
./run_swe_bench_test.sh fix django

# 等价于手动执行：
cd /Users/lihuijin/Desktop/code-agent/mycode
uv run mycode run ~/swe-bench-test/django-test -p "提示词"
```

### Q6: mycode agent 修改了错误的文件怎么办？

```bash
# 查看修改了哪些文件
./run_swe_bench_test.sh diff django

# 全部回退重来
./run_swe_bench_test.sh reset django
```

### Q7: 如何保存/复用成功的修复补丁？

```bash
# 验证通过后自动保存在这里
ls ~/swe-bench-test/fixes/
# 例如: django_20260428_103600.patch

# 应用补丁到新环境
cd <target_repo> && git apply ../fixes/django_xxx.patch
```

### Q8: 如何自定义新的测试用例？

编辑 `run_swe_bench_test.sh` 中的 `get_test_case()` 函数：

```bash
get_test_case() {
  case "$1" in
    my-custom-repo)
      echo "instance_id|base_commit|test_path|description" 
      ;;
    # ... 其他 case
  esac
}
```

---

## 附录：目录结构

```
~/swe-bench-test/                    # 工作根目录
├── django-test/                     # Django 仓库
│   ├── .venv/                       # uv 管理的虚拟环境
│   ├── django/                      # Django 源码
│   └── tests/                       # 测试文件
├── sklearn-test/                    # scikit-learn 仓库
│   ├── .venv/
│   ├── sklearn/
│   └── ...
├── sympy-test/                      # SymPy 仓库
│   ├── .venv/
│   ├── sympy/
│   └── ...
├── fixes/                           # 保存成功的修复补丁 (.patch)
│   ├── django_20260428_xxx.patch
│   └── sklearn_20260428_yyy.patch
├── .swe_bench_env                   # 持久化配置（加速源等）
└── run_swe_bench_test.sh            # 本脚本
```
