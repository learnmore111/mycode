#!/usr/bin/env bash
# ============================================================
# SWE-bench 本地测试脚本 (zsh/bash 兼容)
# 使用方法: ./run_swe_bench_test.sh <command> [repo]
#
# Commands:
#   setup     - 克隆仓库并安装依赖
#   reset     - Git 回退到干净状态
#   test      - 运行失败测试（确认问题存在）
#   verify    - 运行测试验证修复结果
#   diff      - 查看修改内容
#   status    - 查看当前状态
# ============================================================

set -e

WORK_DIR="$HOME/swe-bench-test"
REPO_NAME="${2:-}"

# ==================== GitHub 加速镜像配置 ====================
# 可选值: ghfast | kkgithub | bgithub | gitclone | origin (不加速)
GITHUB_MIRROR="${GITHUB_MIRROR:-ghfast}"  # 默认使用 ghfast 镜像

get_mirror_prefix() {
  case "$1" in
    ghfast)    echo "https://ghfast.top/" ;;
    kkgithub)  echo "https://kkgithub.com/" ;;
    bgithub)   echo "https://bgithub.xyz/" ;;
    gitclone)  echo "https://gitclone.com/github.com/" ;;
    origin|"")echo "" ;;  # 官方源（可能很慢）
    *)         echo "" ;;
  esac
}

# ==================== 工具函数: 仓库 URL/目录映射 ==================== 
get_repo_url() {
  local mirror=$(get_mirror_prefix "$GITHUB_MIRROR")
  case "$1" in
    django)  echo "${mirror}https://github.com/django/django.git" ;;
    sklearn) echo "${mirror}https://github.com/scikit-learn/scikit-learn.git" ;;
    sympy)   echo "${mirror}https://github.com/sympy/sympy.git" ;;
    *)       echo "" ;;
  esac
}

get_repo_dir() {
  case "$1" in
    django)  echo "django-test" ;;
    sklearn) echo "sklearn-test" ;;
    sympy)   echo "sympy-test" ;;
    *)       echo "$1" ;;
  esac
}

# ==================== 工具函数: 获取 mycode 项目路径 ==================== 
MYCODE_PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

get_target_dir() {
  local repo="${1:-}"
  local dir=$(get_repo_dir "$repo")
  echo "$WORK_DIR/$dir"
}

# ==================== 工具函数: 获取测试命令 ====================
get_test_cmd() {
  # 每个仓库的测试命令格式（使用目标仓库自己的 .venv）
  # $1 = repo name, $2 = test path
  local repo="$1"
  local tc_test="$2"
  
  case "$repo" in
    django)
      # Django 必须用 runtests.py，不能直接用 pytest
      echo "cd tests && .venv/bin/python runtests.py handlers -v 2>&1" 
      ;;
    sklearn)
      # scikit-learn 可以用 pytest 但需要指定正确的模块路径
      echo ".venv/bin/python -m pytest $tc_test -x -v --tb=short 2>&1"
      ;;
    sympy)
      # SymPy 标准 pytest
      echo ".venv/bin/python -m pytest $tc_test -x -v --tb=short 2>&1"
      ;;
    *)
      echo ".venv/bin/python -m pytest $tc_test -x -v --tb=short 2>&1"
      ;;
  esac
}

# 获取通用验证命令（运行整个模块测试）
get_verify_cmd() {
  local repo="$1"
  case "$repo" in
    django)   echo "cd tests && .venv/bin/python runtests.py handlers -v 2>&1" ;;
    sklearn)  echo ".venv/bin/python -m pytest sklearn/ensemble/tests/test_forest.py -x -v --tb=short 2>&1" ;;
    sympy)    echo ".venv/bin/python -m pytest sympy/core/tests/test_sympify.py -x -v --tb=short 2>&1" ;;
    *)        echo ".venv/bin/python -m pytest -x -v --tb=short 2>&1" ;;
  esac
}

# ==================== SWE-bench 测试用例配置 ====================
get_test_case() {
  # 参数: repo_name
  # 输出: id|commit|test_path|description (用 | 分隔)
  # 注意: 描述中包含环境说明和测试命令
  case "$1" in
    django)
      echo "django__django-11000|HEAD|tests/handlers/test_base.py|
【Issue 描述】
django/core/handlers/base.py 中间件执行顺序问题。
_get_response() 方法中 _view_middleware 循环应按 LIFO（后进先出）顺序执行。

【⚠️ 环境说明 - 非常重要！】
1. 此仓库使用 Django 自有测试框架，不能用 pytest 直接运行！
2. 正确测试命令: cd tests && .venv/bin/python runtests.py handlers -v
3. 必须使用 .venv/bin/python (Django 自身的虚拟环境)
4. 绝对不要用系统 Python 或其他环境的 Python！

【相关文件】
- django/core/handlers/base.py (_get_response 方法，约第 180-190 行)
- tests/tests.py (参考现有 handler 测试的写法)

【任务步骤】
1. 阅读 django/core/handlers/base.py 的 _get_response 方法
2. 分析 _view_middleware 的循环顺序是否正确
3. 如需修改，改为 reversed(self._view_middleware) 
4. 创建 tests/handlers/test_base.py 编写验证测试（如果不存在）
5. 运行测试验证: cd tests && .venv/bin/python runtests.py handlers -v"
      ;;
    sklearn)
      echo "sklearn__sklearn-22000|HEAD|sklearn/ensemble/tests/test_forest.py|
【Issue 描述】
sklearn RandomForestClassifier.predict() 返回值类型不一致，
在某些边界情况下返回概率数组而非类别标签。

【⚠️ 环境说明 - 重要！】
1. 正确测试命令: .venv/bin/python -m pytest sklearn/ensemble/tests/test_forest.py -xv
2. 必须使用 .venv/bin/python (scikit-learn 自身的虚拟环境)

【相关文件】
- sklearn/ensemble/_forest.py (RandomForestClassifier.predict 方法)
- sklearn/ensemble/tests/test_forest.py

【任务步骤】
1. 检查 predict 方法的实现
2. 找到返回类型不匹配的原因
3. 修复为正确的类别标签返回
4. 运行: .venv/bin/python -m pytest sklearn/ensemble/tests/test_forest.py -xv"
      ;;
    sympy)
      echo "sympy__sympy-11111|HEAD|sympy/core/tests/test_sympify.py|
【Issue 描述】
symPy.core.sympify.sympify() 函数缺少 AttributeError 处理，
导致某些对象转换时崩溃而非优雅降级。

【⚠️ 环境说明 - 重要！】
1. 正确测试命令: .venv/bin/python -m pytest sympy/core/tests/test_sympify.py -xv
2. 必须使用 .venv/bin/python (SymPy 自身的虚拟环境)

【相关文件】
- sympy/core/sympify.py (sympify 函数，约第 500-520 行)
- sympy/core/tests/test_sympify.py

【任务步骤】
1. 查看 sympify 函数的 try/except 块
2. 添加 AttributeError 到异常捕获列表
3. 运行: .venv/bin/python -m pytest sympy/core/tests/test_sympify.py -xv"
      ;;
    *)
      echo ""
      ;;
  esac
}

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
log_ok()    { printf "${GREEN}[OK]${NC} %s\n" "$1"; }
log_warn()  { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
log_err()   { printf "${RED}[ERROR]${NC} %s\n" "$1"; }

# ==================== 命令: setup ==================== 
cmd_setup() {
  local current_mirror="$GITHUB_MIRROR"
  if [ "$current_mirror" = "origin" ] || [ -z "$current_mirror" ]; then
    log_info "🚀 开始设置 SWE-bench 测试环境 (使用 GitHub 官方源)"
  else
    log_info "🚀 开始设置 SWE-bench 测试环境 (使用加速镜像: ${current_mirror})"
  fi
  mkdir -p "$WORK_DIR"

  for repo in django sklearn sympy; do
    dir=$(get_repo_dir "$repo")
    url=$(get_repo_url "$repo")
    target="$WORK_DIR/$dir"

    echo ""
    log_info "=========================================="
    log_info "📦 设置仓库: $repo"
    log_info "=========================================="
    log_info "克隆地址: $url"

    if [ -d "$target" ]; then
      log_warn "目录已存在: $target (跳过克隆)"
    else
      log_info "正在克隆 (使用 ${current_mirror:-origin} 加速)..."
      git clone --depth 1 "$url" "$target"
    fi

    cd "$target"

    # 使用 uv 创建虚拟环境（如果不存在）
    if [ ! -d ".venv" ]; then
      log_info "使用 uv 创建 Python 虚拟环境..."
      uv venv .venv
    fi

    # 使用 uv 安装依赖（兼容 pyproject.toml 和 setup.py）
    log_info "使用 uv 安装依赖..."
    
    case $repo in
      django)
        uv pip install -q -e ".[dev]" --python .venv/bin/python || \
        uv pip install -q -e . --python .venv/bin/python
        ;;
      sklearn)
        # scikit-learn 需要额外安装构建依赖
        uv pip install -q -e ".[dev,test,docs]" --python .venv/bin/python 2>/dev/null || \
        uv pip install -q -e . --python .venv/bin/python
        ;;
      sympy)
        uv pip install -q -e ".[dev]" --python .venv/bin/python 2>/dev/null || \
        uv pip install -q -e ".[all]" --python .venv/bin/python
        ;;
    esac

    log_ok "$repo 环境设置完成 ✓"
    cd "$WORK_DIR"
  done

  echo ""
  log_ok "=========================================="
  log_ok "所有仓库环境设置完成！"
  log_ok "工作目录: $WORK_DIR"
  log_ok "=========================================="
  echo ""
  log_info "下一步 (标准测试流程):"
  log_info "  1. 运行: $0 test <repo>     - 确认问题存在"
  log_info "  2. 运行: $0 dev <repo>      - ★ 启动前端 UI 修复 (浏览器操作)"
  log_info "     或:   $0 fix <repo>      - CLI 交互模式修复"
  log_info "  3. 运行: $0 verify <repo>    - 验证修复结果"
  echo ""
  log_info "示例:"
  log_info "  $0 test django && $0 dev django && $0 verify django"
}

# ==================== 命令: reset (回退) ==================== 
cmd_reset() {
  local repo="${1:-}"
  if [ -z "$repo" ]; then
    log_err "请指定仓库: $0 reset <django|sklearn|sympy>"
    exit 1
  fi

  local dir=$(get_repo_dir "$repo")
  local target="$WORK_DIR/$dir"

  if [ ! -d "$target/.git" ]; then
    log_err "不是有效的 git 仓库: $target"
    exit 1
  fi

  log_info "🔄 回退仓库: $repo"
  cd "$target"

  CURRENT_COMMIT=$(git rev-parse --short HEAD)
  log_info "当前 commit: $CURRENT_COMMIT"
  MOD_COUNT=$(git diff --name-only | wc -l | tr -d ' ')
  UNTRACKED_COUNT=$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')
  log_info "已修改文件: ${MOD_COUNT} 个"
  log_info "未跟踪文件: ${UNTRACKED_COUNT} 个"

  echo ""
  log_info "执行回退操作..."

  if git stash 2>/dev/null; then
    log_ok "已暂存修改 (git stash)"
  else
    log_info "无需暂存"
  fi

  git checkout -- . 2>/dev/null || true
  log_ok "已丢弃工作区修改 (git checkout -- .)"

  git clean -fd 2>/dev/null || true
  log_ok "已清理未跟踪文件 (git clean -fd)"

  NEW_COMMIT=$(git rev-parse --short HEAD)
  echo ""
  log_ok "=========================================="
  log_ok "✅ 回退完成! 当前 commit: $NEW_COMMIT"
  log_ok "=========================================="
  echo ""
  log_info "现在可以重新开始修复测试"
}

# ==================== 命令: test (运行失败测试) ==================== 
cmd_test() {
  local repo="${1:-}"
  if [ -z "$repo" ]; then
    log_err "请指定仓库: $0 test <django|sklearn|sympy>"
    exit 1
  fi

  local dir=$(get_repo_dir "$repo")
  local target="$WORK_DIR/$dir"

  log_info "📋 运行失败测试: $repo"
  cd "$target"
  # 使用 uv run 自动管理虚拟环境，无需手动 activate

  TC_DATA=$(get_test_case "$repo")
  if [ -z "$TC_DATA" ]; then
    log_err "未找到 $repo 的测试配置"
    exit 1
  fi

  tc_id=$(echo "$TC_DATA" | cut -d'|' -f1)
  tc_commit=$(echo "$TC_DATA" | cut -d'|' -f2)
  tc_test=$(echo "$TC_DATA" | cut -d'|' -f3)
  tc_desc=$(echo "$TC_DATA" | cut -d'|' -f4-)

  echo ""
  log_info "------------------------------------------"
  log_info "实例 ID: $tc_id"
  log_info "Base Commit: $tc_commit"
  log_info "失败测试: $tc_test"
  log_info "------------------------------------------"
  echo ""
  log_warn "问题描述:"
  echo "$tc_desc"
  echo ""
  
  log_info "运行测试 (预期应该 FAIL)..."
  echo ""
  
  # 使用仓库专属的测试命令
  local test_cmd=$(get_test_cmd "$repo" "$tc_test")
  log_info "执行: $test_cmd"
  echo ""
  
  set +e
  eval "$test_cmd" | head -60
  TEST_EXIT=$?
  set -e
  
  echo ""
  if [ $TEST_EXIT -ne 0 ]; then
    log_ok "✅ 确认测试失败 (这是预期的，说明问题存在)"
  else
    log_warn "⚠️ 测试通过了 (可能已经修复或 commit 不匹配)"
  fi
}

# ==================== 命令: verify (验证修复) ==================== 
cmd_verify() {
  local repo="${1:-}"
  if [ -z "$repo" ]; then
    log_err "请指定仓库: $0 verify <django|sklearn|sympy>"
    exit 1
  fi

  local dir=$(get_repo_dir "$repo")
  local target="$WORK_DIR/$dir"

  log_info "✅ 验证修复结果: $repo"
  cd "$target"
  # 使用 uv run 自动管理虚拟环境

  TC_DATA=$(get_test_case "$repo")
  if [ -z "$TC_DATA" ]; then
    log_err "未找到 $repo 的测试配置"
    exit 1
  fi

  tc_test=$(echo "$TC_DATA" | cut -d'|' -f3)

  echo ""
  log_info "运行测试 (期望 PASS)..."
  echo ""
  
  # 使用仓库专属的验证命令
  local verify_cmd=$(get_verify_cmd "$repo")
  log_info "执行: $verify_cmd"
  echo ""
  
  set +e
  eval "$verify_cmd" | tail -30
  TEST_EXIT=$?
  set -e
  
  echo ""
  if [ $TEST_EXIT -eq 0 ]; then
    log_ok "=========================================="
    log_ok "🎉 测试通过! 修复成功!"
    log_ok "=========================================="
    
    PATCH_FILE="$WORK_DIR/fixes/${repo}_$(date +%Y%m%d_%H%M%S).patch"
    mkdir -p "$WORK_DIR/fixes"
    git diff > "$PATCH_FILE" 2>/dev/null || true
    log_info "补丁已保存: $PATCH_FILE"
  else
    log_err "=========================================="
    log_err "❌ 测试失败，修复不完整，继续调试"
    log_err "=========================================="
    echo ""
    log_info "建议: 重新启动 mycode agent 补充修复"
    log_info "  cd $target && uv run mycode run -p '补充修复提示词'"
  fi
}

# ==================== 命令: fix (启动 mycode agent 修复) ==================== 
cmd_fix() {
  local repo="${1:-}"
  if [ -z "$repo" ]; then
    log_err "请指定仓库: $0 fix <django|sklearn|sympy>"
    exit 1
  fi

  local target=$(get_target_dir "$repo")

  if [ ! -d "$target/.git" ]; then
    log_err "仓库未初始化: $target (先运行 $0 setup)"
    exit 1
  fi

  TC_DATA=$(get_test_case "$repo")
  tc_desc=$(echo "$TC_DATA" | cut -d'|' -f4-)

  log_info "🤖 启动 mycode agent 修复: $repo"
  log_info "目标目录: $target"
  log_info "mycode 项目: $MYCODE_PROJECT_DIR"
  echo ""
  log_warn "问题描述:"
  echo "$tc_desc" | head -10
  echo ""

  # 检查 mycode 是否可用
  if [ -f "$MYCODE_PROJECT_DIR/pyproject.toml" ]; then
    log_info "正在启动 (交互模式，可直观观察修复过程)..."
    log_info "退出交互模式: 输入 /quit 或 Ctrl+D"
    echo ""
    
    cd "$MYCODE_PROJECT_DIR"
    exec uv run mycode run "$target"
  else
    log_err "找不到 mycode 项目: $MYCODE_PROJECT_DIR"
    exit 1
  fi
}

# ==================== 命令: fix-headless (headless 模式 + 提示词) ==================== 
cmd_fix_headless() {
  local repo="${1:-}"
  if [ -z "$repo" ]; then
    log_err "请指定仓库: $0 fix-h <django|sklearn|sympy>"
    exit 1
  fi

  local prompt="${2:-}"
  if [ -z "$prompt" ]; then
    # 使用内置提示词
    TC_DATA=$(get_test_case "$repo")
    prompt=$(echo "$TC_DATA" | cut -d'|' -f4-)
  fi

  local target=$(get_target_dir "$repo")

  log_info "🤖 启动 mycode headless 模式: $repo"
  
  cd "$MYCODE_PROJECT_DIR"
  uv run mycode run "$target" -p "$prompt"
}

# ==================== 命令: config (配置管理) ==================== 
cmd_config() {
  local action="${1:-show}"
  local GLOBAL_CFG="$HOME/.config/mycode/mycode.jsonc"
  local PROJECT_CFG="$MYCODE_PROJECT_DIR/mycode.json"

  case "$action" in
    show|status)
      log_info "📋 配置状态:"
      echo ""
      printf "  全局配置: " 
      [ -f "$GLOBAL_CFG" ] && echo "${GREEN}✅ $GLOBAL_CFG${NC}" || echo "${RED}❌ 不存在${NC}"
      printf "  项目配置: "
      [ -f "$PROJECT_CFG" ] && echo "${GREEN}✅ $PROJECT_CFG${NC}" || echo "${RED}❌ 不存在${NC}"
      echo ""
      if [ -f "$GLOBAL_CFG" ]; then
        log_info "全局配置内容 (已脱敏 API Key):"
        # 显示配置但隐藏 apiKey
        sed 's/"apiKey": "[^"]*"/apiKey": "***REDACTED***"/' "$GLOBAL_CFG"
      fi
      ;;
    sync|push)
      if [ ! -f "$PROJECT_CFG" ]; then
        log_err "项目配置不存在: $PROJECT_CFG"
        exit 1
      fi
      mkdir -p "$(dirname "$GLOBAL_CFG")"
      cp "$PROJECT_CFG" "$GLOBAL_CFG"
      log_ok "已同步项目配置 → 全局配置"
      log_info "  源: $PROJECT_CFG"
      log_info "  目标: $GLOBAL_CFG"
      ;;
    edit|open)
      if command -v open >/dev/null 2>&1; then
        open "$GLOBAL_CFG"
      else
        ${EDITOR:-nano} "$GLOBAL_CFG"
      fi
      log_info "打开全局配置编辑器"
      ;;
    *)
      log_err "未知操作: $action"
      echo "可用: show, sync, edit"
      exit 1
      ;;
  esac
}

# ==================== 命令: dev (启动前端 UI 修复) ==================== 
cmd_dev() {
  local repo="${1:-}"
  if [ -z "$repo" ]; then
    log_err "请指定仓库: $0 dev <django|sklearn|sympy>"
    exit 1
  fi

  local target=$(get_target_dir "$repo")

  if [ ! -d "$target/.git" ]; then
    log_err "仓库未初始化: $target (先运行 $0 setup)"
    exit 1
  fi

  if [ ! -d "$target/.venv" ]; then
    log_err "虚拟环境不存在: $target/.venv (先运行 $0 setup)"
    exit 1
  fi

  TC_DATA=$(get_test_case "$repo")
  tc_desc=$(echo "$TC_DATA" | cut -d'|' -f4-)

  log_info "🌐 启动 mycode 前端 UI 模式: $repo"
  log_info "目标目录: $target"
  log_info "mycode 项目: $MYCODE_PROJECT_DIR"
  echo ""
  log_info "★ 启动后会自动打开浏览器访问前端界面 ★"
  log_info "  后端 API: http://127.0.0.1:4096"
  log_info "  前端 UI: http://localhost:3000"
  echo ""
  log_warn "问题描述:"
  echo "$tc_desc" | head -8
  echo ""
  log_info "在前端界面中粘贴提示词即可开始修复..."
  log_info "按 Ctrl+C 停止服务"
  echo ""

  # 检查 mycode 是否可用
  if [ ! -f "$MYCODE_PROJECT_DIR/pyproject.toml" ]; then
    log_err "找不到 mycode 项目: $MYCODE_PROJECT_DIR"
    exit 1
  fi

  cd "$MYCODE_PROJECT_DIR"
  # 使用 --directory 指定工作目录为 SWE-bench 目标仓库
  exec uv run mycode dev --directory "$target"
}

# ==================== 命令: diff (查看修改) ==================== 
cmd_diff() {
  local repo="${1:-}"
  if [ -z "$repo" ]; then
    log_err "请指定仓库: $0 diff <django|sklearn|sympy>"
    exit 1
  fi

  local dir=$(get_repo_dir "$repo")
  local target="$WORK_DIR/$dir"

  log_info "📄 查看 $repo 的代码修改:"
  cd "$target"
  
  MODIFIED=$(git diff --name-only)
  if [ -z "$MODIFIED" ]; then
    log_info "没有检测到代码修改"
  else
    log_info "已修改的文件:"
    echo "$MODIFIED"
    echo ""
    log_info "详细 diff:"
    echo "-------------------------------------------"
    git diff
  fi
}

# ==================== 命令: mirror (切换加速源) ==================== 
cmd_mirror() {
  local new_mirror="${1:-}"
  if [ -z "$new_mirror" ]; then
    log_info "当前加速镜像: ${GITHUB_MIRROR:-origin}"
    echo ""
    echo "可用镜像:"
    echo "  ghfast    - ghfast.top (推荐，速度快)"
    echo "  kkgithub - kkgithub.com"
    echo "  bgithub  - bgithub.xyz"
    echo "  gitclone - gitclone.com/github.com/"
    echo "  origin   - GitHub 官方源（不加速）"
    echo ""
    echo "切换方法:"
    echo "  $0 mirror <名称>"
    echo "  或设置环境变量: GITHUB_MIRROR=ghfast $0 setup"
    return
  fi

  case "$new_mirror" in
    ghfast|kkgithub|bgithub|gitclone|origin)
      export GITHUB_MIRROR="$new_mirror"
      # 写入环境变量持久化到 .env 文件
      echo "GITHUB_MIRROR=$new_mirror" > "$WORK_DIR/.swe_bench_env"
      log_ok "已切换到: $new_mirror"
      ;;
    *)
      log_err "无效的镜像: $new_mirror"
      echo "可用: ghfast, kkgithub, bgithub, gitclone, origin"
      exit 1
      ;;
  esac
}

# ==================== 命令: status (状态概览) ==================== 
cmd_status() {
  log_info "📊 SWE-bench 测试环境状态"
  log_info "=========================================="
  printf "当前加速镜像: %s\n" "${GITHUB_MIRROR:-origin}"
  
  for repo in django sklearn sympy; do
    dir=$(get_repo_dir "$repo")
    target="$WORK_DIR/$dir"
    
    echo ""
    if [ ! -d "$target" ]; then
      log_warn "[$repo] ❌ 未初始化 (运行 $0 setup)"
      continue
    fi
    
    cd "$target"
    COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "N/A")
    BRANCH=$(git branch --show-current 2>/dev/null || echo "N/A")
    MODIFIED=$(git diff --name-only | wc -l | tr -d ' ')
    UNTRACKED=$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')
    VENV="❌"
    [ -d ".venv" ] && VENV="✅ (uv)"
    UV_LOCK=""
    [ -f "uv.lock" ] && UV_LOCK=" ✅uv.lock" || UV_LOCK=""
    
    printf "[${repo}] ${GREEN}✅${NC} | Commit: %s | Branch: %s | 修改: %s 文件 | 未跟踪: %s | Venv: %s%s\n" \
      "$COMMIT" "$BRANCH" "$MODIFIED" "$UNTRACKED" "$VENV" "$UV_LOCK"
    cd "$WORK_DIR"
  done
  
  echo ""
  log_info "=========================================="
  log_info "可用命令:"
  echo "  $0 setup              初始化所有仓库"
  echo "  $0 reset  <repo>      回退指定仓库"
  echo "  $0 test  <repo>       运行失败测试"
  echo "  $0 dev   <repo>       ★ 启动前端 UI 修复 (浏览器)"
  echo "  $0 fix   <repo>       启动 CLI 交互模式修复"
  echo "  $0 verify <repo>      验证修复结果"
  echo "  $0 diff  <repo>       查看代码修改"
  echo "  $0 status             显示此状态页"
  echo ""
  log_info "启动 mycode agent 进行修复:"
  echo "  $0 dev django                 # ★ 前端 UI 模式 (推荐体验)"
  echo "  $0 fix django                 # CLI 交互模式"
  echo "  $0 fix-h django               # headless 模式（使用内置提示词）"
  echo "  $0 fix-h django '自定义提示词'  # headless + 自定义提示词"
}

# ==================== 加载持久化配置 ==================== 
if [ -f "$WORK_DIR/.swe_bench_env" ]; then
  source "$WORK_DIR/.swe_bench_env" 2>/dev/null || true
fi

# ==================== 主入口 ==================== 
case "${1:-}" in
  setup)    cmd_setup ;;
  reset)    cmd_reset "$REPO_NAME" ;;
  test)     cmd_test "$REPO_NAME" ;;
  verify)   cmd_verify "$REPO_NAME" ;;
  diff)     cmd_diff "$REPO_NAME" ;;
  fix)      cmd_fix "$REPO_NAME" ;;
  fix-h|fix-headless) cmd_fix_headless "$REPO_NAME" "${3:-}" ;;
  dev|ui)   cmd_dev "$REPO_NAME" ;;  # 前端 UI 模式
  config)   cmd_config "${2:-show}" ;;  # 配置管理 (sync/push/edit/show)
  mirror)   cmd_mirror "$REPO_NAME" ;;  
  status|"") cmd_status ;;
  *)
    echo "用法: $0 <command> [repo]"
    echo ""
    echo "Commands:"
    echo "  setup             克隆仓库并安装依赖 (使用加速镜像)"
    echo "  reset  <repo>     Git 回退到干净状态"
    echo "  test  <repo>      运行失败测试（确认问题存在）"
    echo "  fix   <repo>      启动 mycode agent CLI 交互模式修复"
    echo "  fix-h <repo> [msg] 启动 mycode headless 模式修复"
    echo "  dev   <repo>      ★ 启动 mycode 前端 UI 模式修复 (推荐体验)"
    echo "  config [action]   管理全局配置 (show|sync|edit)"
    echo "  verify <repo>     运行测试验证修复是否成功"
    echo "  diff  <repo>      查看代码修改内容"
    echo "  mirror [name]     查看/切换 GitHub 加速镜像"
    echo "  status            显示环境状态"
    echo ""
    echo "加速镜像: ghfast(默认), kkgithub, bgithub, gitclone, origin"
    echo "Repositories: django, sklearn, sympy"
    echo ""
    echo "测试模式:"
    echo "  CLI 模式:   $0 test django && $0 fix django && $0 verify django"
    echo "  ★ UI 模式: $0 test django && $0 dev django              # 浏览器操作"
    exit 1
    ;;
esac
