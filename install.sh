#!/usr/bin/env bash
# video-transcript skill 一键安装向导(macOS)
# 用法:bash ~/.claude/skills/video-transcript/install.sh

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SKILL_DIR/.env"
PYTHON_BIN="${VT_PY:-python3}"

C_RESET='\033[0m'
C_BOLD='\033[1m'
C_GREEN='\033[32m'
C_YELLOW='\033[33m'
C_RED='\033[31m'
C_BLUE='\033[34m'
C_GRAY='\033[90m'

bar() { printf "${C_GRAY}═══════════════════════════════════════════════════════${C_RESET}\n"; }
sep() { printf "${C_GRAY}───────────────────────────────────────────────────────${C_RESET}\n"; }
ok()  { printf "  ${C_GREEN}✓${C_RESET} %s\n" "$1"; }
warn(){ printf "  ${C_YELLOW}⚠${C_RESET} %s\n" "$1"; }
err() { printf "  ${C_RED}✗${C_RESET} %s\n" "$1"; }
info(){ printf "  ${C_BLUE}ℹ${C_RESET} %s\n" "$1"; }
step(){ printf "\n${C_BOLD}[%s/%s] %s${C_RESET}\n" "$1" "$2" "$3"; }
has_tty(){
  [ "${VIDEO_TRANSCRIPT_NONINTERACTIVE:-0}" != "1" ] &&
    { : </dev/tty; } 2>/dev/null
}

# ── 仅支持 macOS ───────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  err "目前只支持 macOS。Linux/Windows 请看 README.md 手动安装。"
  exit 1
fi

# ── 欢迎 ────────────────────────────────────────────────
bar
printf "${C_BOLD}  🎬 视频逐字稿 Skill 安装向导${C_RESET}\n"
sep
echo "  把 B 站/抖音/小红书/YouTube/视频号、小宇宙播客转成逐字稿"
echo "  转录在你电脑本地跑；链接解析需要联网；视频号首次使用需扫码登录腾讯元宝"
echo ""
echo "  接下来 7 步,大约 6-12 分钟:"
echo "    [1/7] 检查/安装 ffmpeg(视频处理)"
echo "    [2/7] 检查 Python 3"
echo "    [3/7] 装 Python 工具(yt-dlp + playwright)"
echo "    [4/7] 下载浏览器引擎(Chromium, ~300MB)"
echo "    [5/7] 安装 FunASR 转录引擎(纯本地,无需 API Key)"
echo "    [6/7] 安装配套 skill video-download(微信视频号必需)"
echo "    [7/7] 微信视频号元宝登录态(扫码一次,免 Cookie 解析)"
bar
echo ""
if has_tty; then
  read -r -p "  按回车继续 / Ctrl+C 取消..." _ < /dev/tty
fi

# ── Step 1: ffmpeg ─────────────────────────────────────
step 1 7 "检查 ffmpeg"
if command -v ffmpeg >/dev/null 2>&1; then
  FFMPEG_VER=$(ffmpeg -version 2>/dev/null | sed -n '1{s/^ffmpeg version \([^ ]*\).*/\1/;p;}')
  ok "ffmpeg 已装: ${FFMPEG_VER:-unknown}"
else
  warn "ffmpeg 未装,需要 Homebrew 帮忙"
  if ! command -v brew >/dev/null 2>&1; then
    warn "也没装 Homebrew,先帮你装它(macOS 标配工具)"
    info "下一步会让你输入 Mac 开机密码(看不到字符是正常的)"
    if has_tty; then
      read -r -p "  按回车继续..." _ < /dev/tty
    fi
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # 把 brew 加进当前 shell PATH
    if [[ -x /opt/homebrew/bin/brew ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -x /usr/local/bin/brew ]]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
  fi
  info "正在安装 ffmpeg(可能要 1-3 分钟)..."
  brew install ffmpeg
  ok "ffmpeg 装好了"
fi

# ── Step 2: Python 3 ────────────────────────────────────
step 2 7 "检查 Python 3"
if command -v "$PYTHON_BIN" >/dev/null 2>&1 || [ -x "$PYTHON_BIN" ]; then
  PY_VER=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
  PY_OK=$("$PYTHON_BIN" -c 'import sys; print(1 if sys.version_info >= (3,8) else 0)')
  if [[ "$PY_OK" == "1" ]]; then
    ok "Python $PY_VER"
  else
    err "Python $PY_VER 太旧(需要 ≥ 3.8)。建议: brew install python@3.12"
    exit 1
  fi
else
  err "没找到 python3。建议: brew install python@3.12"
  exit 1
fi

# ── Step 3: pip 装 yt-dlp + playwright ─────────────────
step 3 7 "安装 Python 工具"
if "$PYTHON_BIN" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
  PIP_FLAGS=(--break-system-packages --quiet)
else
  PIP_FLAGS=(--user --quiet)
fi
info "yt-dlp ..."
"$PYTHON_BIN" -m pip install "${PIP_FLAGS[@]}" --upgrade yt-dlp
ok "yt-dlp"

info "playwright ..."
"$PYTHON_BIN" -m pip install "${PIP_FLAGS[@]}" --upgrade playwright
ok "playwright"

# ── Step 4: chromium ────────────────────────────────────
step 4 7 "下载 Chromium(playwright 用的浏览器引擎, ~300MB)"
info "国内网络可能稍慢,大概 1-3 分钟..."
"$PYTHON_BIN" -m playwright install chromium
ok "Chromium 装好"

# ── Step 5: funasr 转录引擎 ────────────────────────────
step 5 7 "安装 FunASR 转录引擎(SenseVoice-Small,约 234M)"
sep
info "安装 funasr + torchaudio(纯本地转录,不需要 API Key)..."
"$PYTHON_BIN" -m pip install "${PIP_FLAGS[@]}" --upgrade funasr torchaudio
ok "funasr 装好"
info "视频转录模型 SenseVoice-Small(234M)首次转录时自动下载"
info "播客说话人分离模型 paraformer/CAM++/VAD/punc(约 1GB)首次转播客时自动下载"

# 首次安装才创建 .env；升级/重跑安装器必须保留用户热词与私有配置。
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# video-transcript skill 配置
# 由 install.sh 生成于 $(date '+%Y-%m-%d %H:%M:%S')

# 可选:热词列表(空格分隔),提升专有名词/人名/术语识别率
# 例:FUNASR_HOTWORD=玉伯 优麦 YouMind WorkBuddy Codex
# FUNASR_HOTWORD=
EOF
  chmod 600 "$ENV_FILE"
  ok "已创建 $ENV_FILE (chmod 600,只有你能读)"
else
  chmod 600 "$ENV_FILE"
  ok "保留现有 $ENV_FILE"
fi

# ── Step 6: 配套 skill video-download(微信视频号必需) ──
step 6 7 "安装配套 skill video-download(微信视频号下载)"
sep
VD_TARGET="${VIDEO_DOWNLOAD_HOME:-$(dirname "$SKILL_DIR")/video-download}"
case "$VD_TARGET" in
  /*/video-download)
    ;;
  *)
    err "VIDEO_DOWNLOAD_HOME 必须是以 /video-download 结尾的绝对路径: $VD_TARGET"
    exit 1
    ;;
esac
if [ -d "$VD_TARGET" ] && [ -f "$VD_TARGET/scripts/download_video.py" ]; then
  ok "video-download 已存在: $VD_TARGET"
else
  info "拉取 video-download skill(抖音/小红书/B站/YouTube/微信视频号 → 本地 MP4)..."
  mkdir -p "$(dirname "$VD_TARGET")"
  VD_STAGE_ROOT=$(mktemp -d)
  VD_STAGE="$VD_STAGE_ROOT/video-download"
  if command -v git >/dev/null 2>&1 && git clone --depth=1 https://github.com/Backtthefuture/video-download.git "$VD_STAGE" 2>&1; then
    VD_SOURCE="$VD_STAGE"
  elif curl -fsSL https://github.com/Backtthefuture/video-download/archive/refs/heads/main.tar.gz | tar xz -C "$VD_STAGE_ROOT" 2>&1; then
    VD_SKILL_FILE=$(find "$VD_STAGE_ROOT" -maxdepth 2 -type f -name SKILL.md -print -quit)
    VD_SOURCE="${VD_SKILL_FILE:+$(dirname "$VD_SKILL_FILE")}"
  else
    VD_SOURCE=""
  fi
  if [ -n "${VD_SOURCE:-}" ] && [ -f "$VD_SOURCE/scripts/download_video.py" ]; then
    mkdir -p "$VD_TARGET"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --exclude='.git/' --exclude='.env' "$VD_SOURCE/" "$VD_TARGET/"
    else
      warn "缺少 rsync，无法安全更新已有的 video-download 目录"
    fi
  fi
  rm -rf "$VD_STAGE_ROOT"
  if [ -d "$VD_TARGET" ] && [ -f "$VD_TARGET/scripts/download_video.py" ]; then
    ok "video-download 就绪: $VD_TARGET"
  else
    warn "video-download 拉取失败,可稍后手动安装(仅影响 --keep-video / 只下载)"
  fi
fi

# video-transcript 的公开发行默认只走本机元宝登录态。
# 公共 Worker 当前需要额外服务器凭据，不能再作为新用户默认路线。
if [ -d "$VD_TARGET" ]; then
  VD_ENV="$VD_TARGET/.env"
  if [ ! -f "$VD_ENV" ]; then
    cat > "$VD_ENV" <<'EOF'
# video-download skill 配置
# yuanbao-login: 复用本机元宝登录态，链接只发给腾讯官方域名
# cookie: 用本机元宝 Cookie,隐私更好,需配置 SPH_COOKIE/YUANBAO_COOKIE
# 元宝登录态: 见 install.sh Step 7 / sph_resolver.py --login(扫码一次)
WECHAT_RESOLVER=yuanbao-login
EOF
    chmod 600 "$VD_ENV"
    ok "已写入 $VD_ENV (WECHAT_RESOLVER=yuanbao-login)"
  elif grep -Eq '^[[:space:]]*WECHAT_RESOLVER=public-worker[[:space:]]*$' "$VD_ENV"; then
    "$PYTHON_BIN" - "$VD_ENV" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
updated = []
for line in lines:
    if line.strip() == "WECHAT_RESOLVER=public-worker":
        updated.append("WECHAT_RESOLVER=yuanbao-login")
    else:
        updated.append(line)
path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
    chmod 600 "$VD_ENV"
    warn "检测到旧的 public-worker 默认值，已只迁移为 yuanbao-login；其他配置保持不变"
  else
    chmod 600 "$VD_ENV"
    ok "保留现有 $VD_ENV"
  fi
fi

# ── Step 7: 微信视频号元宝登录态(扫码一次,免 Cookie 解析) ──
step 7 7 "微信视频号元宝登录态(扫码一次,以后免 Cookie 解析视频号)"
sep
SPH_SCRIPT="$SKILL_DIR/scripts/sph_resolver.py"
if [ ! -f "$SPH_SCRIPT" ]; then
  warn "缺少 sph_resolver.py,跳过元宝登录态配置"
  warn "视频号链接暂不可用；本地文件及其他平台不受影响"
else
  # 探测可用的 python(优先当前安装解释器，其次 WorkBuddy venv)
  VENV_PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
  RESOLVER_PY="$PYTHON_BIN"
  if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import playwright" 2>/dev/null; then
    RESOLVER_PY="$VENV_PY"
  elif "$PYTHON_BIN" -c "import playwright" 2>/dev/null; then
    RESOLVER_PY="$PYTHON_BIN"
  else
    warn "当前 python 环境没有 playwright,无法弹出扫码;可用 venv python 手动执行:"
    warn "  $PYTHON_BIN $SPH_SCRIPT --login"
    RESOLVER_PY=""
  fi

  if [ -n "$RESOLVER_PY" ]; then
    info "检查现有登录态..."
    if "$RESOLVER_PY" "$SPH_SCRIPT" --check 2>/dev/null | grep -q '"loggedIn": true'; then
      ok "元宝登录态已存在,无需重新扫码"
    else
      echo ""
      if has_tty; then
        info "视频号首次使用需弹出腾讯元宝登录页，请用微信扫码一次"
        info "只保存本机登录态，不把 Cookie 发给第三方 Worker"
        echo ""
        read -r -p "  按回车弹出扫码窗口 / 输入 s 跳过(以后手动运行 --login)..." CHOICE < /dev/tty
        if [[ "${CHOICE:-}" != "s" && "${CHOICE:-}" != "S" ]]; then
          if "$RESOLVER_PY" "$SPH_SCRIPT" --login; then
            ok "元宝登录态已建立，视频号认证可用"
          else
            warn "扫码登录未完成,可稍后手动运行: $RESOLVER_PY $SPH_SCRIPT --login"
          fi
        else
          warn "已跳过,以后需要视频号时运行: $RESOLVER_PY $SPH_SCRIPT --login"
        fi
      else
        warn "当前是非交互安装，未自动打开扫码窗口"
        warn "以后需要视频号时运行: $RESOLVER_PY $SPH_SCRIPT --login"
      fi
    fi
  fi
fi

# ── 完成 + 自检 ─────────────────────────────────────────
echo ""
bar
printf "${C_BOLD}  ✅ 安装完成,跑一次自检...${C_RESET}\n"
sep
"$PYTHON_BIN" "$SKILL_DIR/scripts/transcript.py" --doctor

echo ""
bar
printf "${C_BOLD}  🎉 核心转录环境安装完成${C_RESET}\n"
sep
cat <<EOF
  试一下:
    在 Claude Code 里输入: /video-transcript <视频URL>

    或终端直接跑:
    $PYTHON_BIN $SKILL_DIR/scripts/transcript.py <URL>

  逐字稿默认存到: $SKILL_DIR/outputs/

  微信视频号:
    视频号首次使用需建立本机元宝登录态
    登录/续期: $PYTHON_BIN $SKILL_DIR/scripts/sph_resolver.py --login
    真实链路验收: $PYTHON_BIN $SKILL_DIR/scripts/transcript.py --doctor-live <公开视频号链接>

  常见问题: cat $SKILL_DIR/README.md
EOF
bar
