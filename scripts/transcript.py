#!/usr/bin/env python3
"""Cloud-only entry point. No local ASR imports or fallback."""
import os
import sys
import json
import subprocess
from pathlib import Path
SKILL_DIR = str(Path(__file__).resolve().parents[1])
def find_video_download_script():
    candidates = []
    explicit_home = os.getenv("VIDEO_DOWNLOAD_HOME")
    if explicit_home:
        candidates.append(os.path.join(os.path.expanduser(explicit_home), "scripts", "download_video.py"))
    # 优先使用和当前 video-transcript 同一安装根下的配套副本，避免命中其他运行时的旧版本。
    candidates.extend([
        os.path.join(os.path.dirname(SKILL_DIR), "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".agents", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".Codex", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".codex", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".claude", "skills", "video-download", "scripts", "download_video.py"),
    ])
    seen = set()
    for path in candidates:
        path = os.path.abspath(path)
        if path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            return path
    return None


def _run_video_download_json(args, timeout=900):
    script = find_video_download_script()
    if not script:
        raise RuntimeError("找不到 video-download/scripts/download_video.py")
    cmd = [sys.executable, script] + args + ["--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        err = ""
        try:
            data = json.loads((r.stdout or "").strip())
            err = data.get("error") or ""
        except Exception:
            err = ((r.stderr or r.stdout or "").strip().splitlines() or [""])[-1]
        raise RuntimeError(f"video-download 失败: {err or '未知错误'}")
    data = json.loads(r.stdout.strip())
    if not data.get("ok"):
        raise RuntimeError(f"video-download 失败: {data.get('error') or '未知错误'}")
    return data


def download_via_video_download(url):
    args = [url]
    # video-transcript 的公开发行默认只走本机元宝登录态。旧安装里的
    # WECHAT_RESOLVER=public-worker 不能覆盖这里，除非用户显式设置本变量。
    resolver = (os.getenv("VIDEO_DOWNLOAD_WECHAT_RESOLVER") or "yuanbao-login").strip()
    args += ["--wechat-resolver", resolver or "yuanbao-login"]
    data = _run_video_download_json(args, timeout=1200)
    path = data.get("path")
    if not path or not os.path.exists(path):
        raise RuntimeError("video-download 未返回有效本地视频路径")
    return path, data.get("title") or ""



from quark_transcript import main
if __name__ == "__main__":
    sys.exit(main())
