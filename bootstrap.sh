#!/usr/bin/env bash
# video-transcript skill 一行命令安装入口
#
# 用法:
#   bash <(curl -fsSL https://raw.githubusercontent.com/Backtthefuture/video-transcript/main/bootstrap.sh)
#
# 流程: 拉 skill 文件 → 跑 install.sh(装系统依赖 + FunASR 转录引擎)
#
# 拉取顺序: git clone 暂存 → GitHub tarball；更新时保留本机配置和产物

set -euo pipefail

REPO="Backtthefuture/video-transcript"
SKILL="video-transcript"
TARGET="${VIDEO_TRANSCRIPT_TARGET:-$HOME/.claude/skills/$SKILL}"

C_BOLD='\033[1m'; C_GREEN='\033[32m'; C_YELLOW='\033[33m'; C_RED='\033[31m'; C_BLUE='\033[34m'; C_GRAY='\033[90m'; C_RESET='\033[0m'
say()  { printf "${C_BLUE}▸${C_RESET} %s\n" "$1"; }
ok()   { printf "  ${C_GREEN}✓${C_RESET} %s\n" "$1"; }
warn() { printf "  ${C_YELLOW}⚠${C_RESET} %s\n" "$1"; }
err()  { printf "  ${C_RED}✗${C_RESET} %s\n" "$1"; }
bar()  { printf "${C_GRAY}═══════════════════════════════════════════════════════${C_RESET}\n"; }

# ── Codex 集成:检测到 ~/.codex/ 就注册 /video-transcript ──
register_codex() {
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  if [ ! -d "$codex_home" ]; then
    printf "  ${C_GRAY}ℹ${C_RESET} 未检测到 ~/.codex/,跳过 Codex 集成(只 Claude Code 可用)\n"
    return 0
  fi
  mkdir -p "$codex_home/prompts"
  cat > "$codex_home/prompts/video-transcript.md" <<PROMPT_EOF
Use the video-transcript skill at:

    $TARGET/SKILL.md

Read that SKILL.md before acting and follow its current platform routing,
privacy boundaries, error handling, and output contract. Do not reproduce an
older workflow from this prompt. The user's request is:

    \$ARGUMENTS
PROMPT_EOF
  ok "已注册 Codex 命令 → $codex_home/prompts/video-transcript.md"
  printf "  ${C_GRAY}  在 Codex 里可用 /video-transcript <URL> 触发${C_RESET}\n"
}

bar
printf "${C_BOLD}  🎬 video-transcript skill 安装引导${C_RESET}\n"
bar
echo ""

case "$TARGET" in
  ""|/|"$HOME"|"$HOME/")
    err "拒绝使用过宽的安装目标: $TARGET"
    exit 1
    ;;
  /*/video-transcript)
    ;;
  *)
    err "安装目标必须是以 /video-transcript 结尾的绝对路径: $TARGET"
    exit 1
    ;;
esac

TARGET_PARENT=$(dirname "$TARGET")
mkdir -p "$TARGET_PARENT"
STAGE_ROOT=$(mktemp -d)
STAGE_TARGET="$STAGE_ROOT/video-transcript"
cleanup_stage(){ rm -rf "$STAGE_ROOT"; }
trap cleanup_stage EXIT

fetched=""
if command -v git >/dev/null 2>&1; then
  say "暂存拉取 GitHub main..."
  if git clone --depth=1 "https://github.com/$REPO.git" "$STAGE_TARGET" 2>&1; then
    fetched="git"
  fi
fi

if [ -z "$fetched" ]; then
  say "git 不可用或拉取失败，改用 GitHub tarball..."
  if curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar xz -C "$STAGE_ROOT" 2>&1; then
    SKILL_FILE=$(find "$STAGE_ROOT" -maxdepth 2 -type f -name SKILL.md -print -quit)
    SUBDIR="${SKILL_FILE:+$(dirname "$SKILL_FILE")}"
    if [ -n "$SUBDIR" ] && [ -d "$SUBDIR" ]; then
      STAGE_TARGET="$SUBDIR"
      fetched="tarball"
    fi
  fi
fi

if [ -z "$fetched" ] || [ ! -f "$STAGE_TARGET/SKILL.md" ]; then
  err "GitHub main 拉取失败!"
  err "请手动 git clone 后跑: bash <skill-dir>/install.sh"
  err "  git clone https://github.com/$REPO ~/Downloads/video-transcript"
  err "  bash ~/Downloads/video-transcript/install.sh"
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  err "缺少 rsync，无法安全保留已有 .env 与 outputs/"
  exit 1
fi

if [ -d "$TARGET" ]; then
  say "检测到已有安装，更新程序文件并保留 .env、outputs 和本地词表..."
else
  say "安装到 $TARGET ..."
  mkdir -p "$TARGET"
fi

rsync -a \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='outputs/' \
  --exclude='__pycache__/' \
  --exclude='.podcast_glossary.json' \
  "$STAGE_TARGET/" "$TARGET/"
ok "程序文件已同步到 $TARGET"

echo ""
say "进入安装向导(装系统依赖 + FunASR 转录引擎)..."
echo ""
bash "$TARGET/install.sh"

echo ""
register_codex
