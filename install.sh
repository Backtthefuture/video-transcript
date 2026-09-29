#!/usr/bin/env bash
# Optional setup for the cloud version. The Quark Drive Skill is installed separately.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${VT_PY:-python3}"
VD_TARGET="${VIDEO_DOWNLOAD_HOME:-$(dirname "$SKILL_DIR")/video-download}"
QUARK_HOME="${QUARK_SKILL_HOME:-}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "需要 Python 3.9+；设置 VT_PY 可指定解释器。" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 9))'; then
  echo "Python 版本过低，需要 3.9+。" >&2
  exit 1
fi
for tool in node ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "缺少 $tool。请先安装，再重试。" >&2
    exit 1
  fi
done

case "$VD_TARGET" in
  /*/video-download) ;;
  *) echo "VIDEO_DOWNLOAD_HOME 必须指向以 /video-download 结尾的绝对路径。" >&2; exit 1 ;;
esac
if [ ! -f "$VD_TARGET/scripts/download_video.py" ]; then
  if [ -e "$VD_TARGET" ]; then
    echo "目标目录已存在但不是可用的 video-download Skill：$VD_TARGET" >&2
    exit 1
  fi
  command -v git >/dev/null 2>&1 || { echo "需要 git 安装配套的 video-download Skill。" >&2; exit 1; }
  mkdir -p "$(dirname "$VD_TARGET")"
  git clone --depth=1 https://github.com/Backtthefuture/video-download.git "$VD_TARGET"
fi

# Dependencies needed by the downloader. Use the selected Python environment.
if ! "$PYTHON_BIN" -c 'import yt_dlp, playwright' >/dev/null 2>&1; then
  echo "为 video-download 安装 Python 依赖到 $PYTHON_BIN ..."
  "$PYTHON_BIN" -m pip install -r "$SKILL_DIR/requirements.txt"
fi
"$PYTHON_BIN" -m playwright install chromium

# Never overwrite user configuration or choose a third-party WeChat worker implicitly.
VD_ENV="$VD_TARGET/.env"
if [ ! -f "$VD_ENV" ]; then
  printf '%s\n' 'WECHAT_RESOLVER=yuanbao-login' > "$VD_ENV"
  chmod 600 "$VD_ENV"
fi

if [ -z "$QUARK_HOME" ]; then
  for candidate in "$HOME/.agents/skills/quarkclouddrive" "$HOME/.codex/skills/quarkclouddrive" "$HOME/.workbuddy/skills/quarkclouddrive" "$(dirname "$SKILL_DIR")/quarkclouddrive"; do
    if [ -f "$candidate/SKILL.md" ]; then QUARK_HOME="$candidate"; break; fi
  done
fi
if [ -z "$QUARK_HOME" ] || [ ! -f "$QUARK_HOME/scripts/quark-drive.cjs" ]; then
  echo "还需要单独安装并授权夸克网盘官方 Skill（quarkclouddrive）。" >&2
  echo "安装说明见 README.md；此脚本不会复制夸克凭据。" >&2
fi

"$PYTHON_BIN" "$SKILL_DIR/scripts/transcript.py" --doctor
