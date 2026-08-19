#!/usr/bin/env bash
# video-transcript skill 一键安装向导(macOS)
# 用法:bash ~/.claude/skills/video-transcript/install.sh

set -e

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SKILL_DIR/.env"

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

# ── 仅支持 macOS ───────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  err "目前只支持 macOS。Linux/Windows 请看 README.md 手动安装。"
  exit 1
fi

# ── 欢迎 ────────────────────────────────────────────────
bar
printf "${C_BOLD}  🎬 视频逐字稿 Skill 安装向导${C_RESET}\n"
sep
echo "  把 B 站/抖音/小红书/YouTube 视频、小宇宙播客自动转成逐字稿"
echo "  全程在你电脑后台跑,不弹窗、不要登录视频网站"
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
read -r -p "  按回车继续 / Ctrl+C 取消..." _ < /dev/tty || true

# ── Step 1: ffmpeg ─────────────────────────────────────
step 1 7 "检查 ffmpeg"
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg 已装: $(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')"
else
  warn "ffmpeg 未装,需要 Homebrew 帮忙"
  if ! command -v brew >/dev/null 2>&1; then
    warn "也没装 Homebrew,先帮你装它(macOS 标配工具)"
    info "下一步会让你输入 Mac 开机密码(看不到字符是正常的)"
    read -r -p "  按回车继续..." _ < /dev/tty || true
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
if command -v python3 >/dev/null 2>&1; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
  PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,8) else 0)')
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
if python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
  PIP_FLAGS="--break-system-packages --quiet"
else
  PIP_FLAGS="--user --quiet"
fi
info "yt-dlp ..."
python3 -m pip install $PIP_FLAGS --upgrade yt-dlp
ok "yt-dlp"

info "playwright ..."
python3 -m pip install $PIP_FLAGS --upgrade playwright
ok "playwright"

# ── Step 4: chromium ────────────────────────────────────
step 4 7 "下载 Chromium(playwright 用的浏览器引擎, ~300MB)"
info "国内网络可能稍慢,大概 1-3 分钟..."
python3 -m playwright install chromium
ok "Chromium 装好"

# ── Step 5: funasr 转录引擎 ────────────────────────────
step 5 7 "安装 FunASR 转录引擎(SenseVoice-Small,约 234M)"
sep
info "安装 funasr + torchaudio(纯本地转录,不需要 API Key)..."
python3 -m pip install $PIP_FLAGS --upgrade funasr torchaudio
ok "funasr 装好"
info "视频转录模型 SenseVoice-Small(234M)首次转录时自动下载"
info "播客说话人分离模型 paraformer/CAM++/VAD/punc(约 1GB)首次转播客时自动下载"

# 写入最小 .env(可选热词)
cat > "$ENV_FILE" <<EOF
# video-transcript skill 配置
# 由 install.sh 生成于 $(date '+%Y-%m-%d %H:%M:%S')

# 可选:热词列表(空格分隔),提升专有名词/人名/术语识别率
# 例:FUNASR_HOTWORD=玉伯 优麦 YouMind WorkBuddy Codex
# FUNASR_HOTWORD=
EOF
chmod 600 "$ENV_FILE"
ok "已写入 $ENV_FILE (chmod 600,只有你能读)"

# ── Step 6: 配套 skill video-download(微信视频号必需) ──
step 6 7 "安装配套 skill video-download(微信视频号下载)"
sep
VD_TARGET="$HOME/.claude/skills/video-download"
if [ -d "$VD_TARGET" ] && [ -f "$VD_TARGET/scripts/download_video.py" ]; then
  ok "video-download 已存在: $VD_TARGET"
else
  info "拉取 video-download skill(抖音/小红书/B站/YouTube/微信视频号 → 本地 MP4)..."
  if command -v npx >/dev/null 2>&1 && npx -y skills add Backtthefuture/video-download -a claude-code -g -y 2>&1; then
    :
  elif git clone --depth=1 https://github.com/Backtthefuture/video-download.git "$VD_TARGET" 2>&1; then
    rm -rf "$VD_TARGET/.git"
  else
    warn "video-download 拉取失败,可稍后手动安装(仅影响微信视频号转录)"
  fi
  if [ -d "$VD_TARGET" ] && [ -f "$VD_TARGET/scripts/download_video.py" ]; then
    ok "video-download 就绪"
  fi
fi

# 写 video-download 的最小 .env(默认 public-worker,无需 Cookie;失效时自动回退元宝登录态)
if [ -d "$VD_TARGET" ]; then
  VD_ENV="$VD_TARGET/.env"
  if [ ! -f "$VD_ENV" ]; then
    cat > "$VD_ENV" <<'EOF'
# video-download skill 配置
# public-worker: 走公共 Worker 解析视频号,无需本机 Cookie(链接会发给第三方);失效时自动回退元宝登录态
# cookie: 用本机元宝 Cookie,隐私更好,需配置 SPH_COOKIE/YUANBAO_COOKIE
# 元宝登录态: 见 install.sh Step 7 / sph_resolver.py --login(扫码一次,官方接口解析)
WECHAT_RESOLVER=public-worker
EOF
    chmod 600 "$VD_ENV"
    ok "已写入 $VD_ENV (WECHAT_RESOLVER=public-worker)"
  fi
fi

# ── Step 7: 微信视频号元宝登录态(扫码一次,免 Cookie 解析) ──
step 7 7 "微信视频号元宝登录态(扫码一次,以后免 Cookie 解析视频号)"
sep
SPH_SCRIPT="$SKILL_DIR/scripts/sph_resolver.py"
if [ ! -f "$SPH_SCRIPT" ]; then
  warn "缺少 sph_resolver.py,跳过元宝登录态配置"
  warn "视频号解析将依赖公共 Worker(已失效)或手动配置 SPH_COOKIE"
else
  # 探测可用的 python(优先 WorkBuddy venv)
  VENV_PY="/Users/superhuang/.workbuddy/binaries/python/envs/default/bin/python"
  RESOLVER_PY="$python3"
  if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import playwright" 2>/dev/null; then
    RESOLVER_PY="$VENV_PY"
  elif python3 -c "import playwright" 2>/dev/null; then
    RESOLVER_PY="python3"
  else
    warn "当前 python 环境没有 playwright,无法弹出扫码;可用 venv python 手动执行:"
    warn "  python3 $SPH_SCRIPT --login"
    RESOLVER_PY=""
  fi

  if [ -n "$RESOLVER_PY" ]; then
    info "检查现有登录态..."
    if "$RESOLVER_PY" "$SPH_SCRIPT" --check 2>/dev/null | grep -q '"loggedIn": true'; then
      ok "元宝登录态已存在,无需重新扫码"
    else
      echo ""
      info "即将弹出腾讯元宝官方登录页,请用微信扫码登录(只需这一次)"
      info "登录后 Skill 会把登录态保存到 ~/.workbuddy/credentials/,之后解析视频号免扫码"
      echo ""
      read -r -p "  按回车弹出扫码窗口 / 输入 s 跳过(以后手动运行 --login)..." CHOICE < /dev/tty || true
      if [[ "${CHOICE:-}" != "s" && "${CHOICE:-}" != "S" ]]; then
        if "$RESOLVER_PY" "$SPH_SCRIPT" --login; then
          ok "元宝登录态已建立,视频号解析免 Cookie 可用"
        else
          warn "扫码登录未完成,可稍后手动运行: python3 $SPH_SCRIPT --login"
        fi
      else
        warn "已跳过,以后需要时手动运行: python3 $SPH_SCRIPT --login"
      fi
    fi
  fi
fi

# ── 完成 + 自检 ─────────────────────────────────────────
echo ""
bar
printf "${C_BOLD}  ✅ 安装完成,跑一次自检...${C_RESET}\n"
sep
python3 "$SKILL_DIR/scripts/transcript.py" --doctor

echo ""
bar
printf "${C_BOLD}  🎉 一切就绪!${C_RESET}\n"
sep
cat <<EOF
  试一下:
    在 Claude Code 里输入: /video-transcript <视频URL>

    或终端直接跑:
    python3 $SKILL_DIR/scripts/transcript.py <URL>

  逐字稿默认存到: $SKILL_DIR/outputs/

  微信视频号:
    已建立元宝登录态的话,直接粘贴链接即可转录,免 Cookie 免扫码
    登录态过期时,运行: python3 $SKILL_DIR/scripts/sph_resolver.py --login

  常见问题: cat $SKILL_DIR/README.md
EOF
bar
