#!/usr/bin/env bash
# video-transcript skill 一行命令安装入口
#
# 用法:
#   bash <(curl -fsSL https://raw.githubusercontent.com/Backtthefuture/video-transcript/main/bootstrap.sh)
#
# 流程: 拉 skill 文件 → 跑 install.sh(装系统依赖 + FunASR 转录引擎)
#
# 兜底顺序: npx skills add → git clone → tarball

set -e

REPO="Backtthefuture/video-transcript"
SKILL="video-transcript"
TARGET="$HOME/.claude/skills/$SKILL"

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
  cat > "$codex_home/prompts/video-transcript.md" <<'PROMPT_EOF'
You are a video transcript extractor. The user provides a video URL
(B站 / 抖音 / 小红书 / YouTube / 微信视频号) or a local file path as $ARGUMENTS.

If the URL is a WeChat Channels link (weixin.qq.com/sph or channels.weixin.qq.com),
read ~/.claude/skills/video-transcript/skills/weixin-layout.md and follow those
three modes. Default (no extra words, or 逐字稿): polished spoken transcript in
chat, no PDF. 文字PDF / 文字版本: same transcript as Kami parchment PDF.
截图PDF / 截图版本: same transcript + video keyframes; add --keep-video.
Never rewrite 视频号 body as 导读 / overview / Takeaways.

Otherwise run this command, streaming both stderr and stdout:

    python3 ~/.claude/skills/video-transcript/scripts/transcript.py "$ARGUMENTS"

Behavior contract:
1. The script prints a 📊 评估表 to stderr early on. As soon as you see it,
   tell the user the title, duration, segment count, and 预估耗时 — so they
   know how long to wait.
2. When the script finishes, the FULL markdown transcript is on stdout,
   starting with a `# <title>` heading. You MUST display the entire transcript
   verbatim — every section with its `[MM:SS - MM:SS]` header. Do NOT summarize.
   Do NOT only say "saved to xxx.md".
3. End your reply with one short line noting the saved .md path.

If the script fails with missing dependencies, suggest the user run:
    bash ~/.claude/skills/video-transcript/install.sh
PROMPT_EOF
  ok "已注册 Codex 命令 → $codex_home/prompts/video-transcript.md"
  printf "  ${C_GRAY}  在 Codex 里可用 /video-transcript <URL> 触发${C_RESET}\n"
}

bar
printf "${C_BOLD}  🎬 video-transcript skill 安装引导${C_RESET}\n"
bar
echo ""

# ── 已安装则提示 ─────────────────────────────────────────
if [ -d "$TARGET" ]; then
  warn "已检测到 $TARGET 存在"
  printf "  覆盖重装? [y/N]: "
  read -r yn < /dev/tty || yn=""
  case "$yn" in
    [Yy]*) say "覆盖中,删除旧目录..."; rm -rf "$TARGET" ;;
    *) say "保留现有 skill,只跑 install.sh 重新配置"
       bash "$TARGET/install.sh"
       echo ""
       register_codex
       exit 0 ;;
  esac
fi

mkdir -p "$HOME/.claude/skills"

# ── 三档兜底拉 skill 文件 ────────────────────────────────
fetched=""

# 档 1: npx skills add(独立 skill 仓库,根目录即 SKILL.md)
if [ -z "$fetched" ] && command -v npx >/dev/null 2>&1; then
  say "用 npx skills add 拉 skill..."
  if npx -y skills add "$REPO" -a claude-code -g -y 2>&1; then
    if [ -d "$TARGET" ] && [ -f "$TARGET/SKILL.md" ]; then
      fetched="npx"
      ok "通过 npx skills 拉到 $TARGET"
    fi
  fi
  [ -z "$fetched" ] && warn "npx skills 失败,尝试下一档..."
fi

# 档 2: git clone(仓库根目录即 skill,直接拉全量)
if [ -z "$fetched" ] && command -v git >/dev/null 2>&1; then
  say "用 git clone 拉 skill..."
  if git clone --depth=1 "https://github.com/$REPO.git" "$TARGET" 2>&1; then
    rm -rf "$TARGET/.git"
    fetched="git"
    ok "通过 git clone 拉到 $TARGET"
  fi
  [ -z "$fetched" ] && warn "git 失败,尝试下一档..."
fi

# 档 3: tarball(终极兜底,无需 git/node)
if [ -z "$fetched" ]; then
  say "用 tarball 下载..."
  TMP=$(mktemp -d)
  if curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar xz -C "$TMP" 2>&1; then
    SUBDIR=$(find "$TMP" -maxdepth 2 -type f -name SKILL.md -exec dirname {} \; | head -1)
    if [ -n "$SUBDIR" ] && [ -d "$SUBDIR" ]; then
      mv "$SUBDIR" "$TARGET"
      rm -rf "$TMP"
      fetched="tarball"
      ok "通过 tarball 拉到 $TARGET"
    fi
  fi
fi

if [ -z "$fetched" ]; then
  err "三种方式都失败了!"
  err "请手动 git clone 后跑: bash <skill-dir>/install.sh"
  err "  git clone https://github.com/$REPO ~/Downloads/video-transcript"
  err "  bash ~/Downloads/video-transcript/install.sh"
  exit 1
fi

echo ""
say "进入安装向导(装系统依赖 + FunASR 转录引擎)..."
echo ""
bash "$TARGET/install.sh"

echo ""
register_codex
