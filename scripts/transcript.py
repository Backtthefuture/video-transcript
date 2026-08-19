#!/usr/bin/env python3
"""
视频逐字稿提取工具
输入链接/本地文件 → 解析一次 → 直链提音频(+模型预热并行) → FunASR 转录 → 机器预整理
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(SKILL_DIR, "outputs")
ENV_FILE = os.path.join(SKILL_DIR, ".env")
CACHE_INDEX = os.path.join(DEFAULT_OUTPUT_DIR, ".cache", "index.json")
WORK_DIR = "/tmp/video-transcript"

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            os.environ.setdefault(k, v)


_load_dotenv(ENV_FILE)
FUNASR_HOTWORD = os.getenv("FUNASR_HOTWORD") or None
WECHAT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def check_ytdlp():
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def is_url(path):
    return str(path).startswith("http://") or str(path).startswith("https://")


def detect_platform(url):
    url_lower = (url or "").lower()
    if "bilibili.com" in url_lower or "b23.tv" in url_lower:
        return "bilibili"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "xiaohongshu.com" in url_lower or "xhslink.com" in url_lower:
        return "xiaohongshu"
    if "douyin.com" in url_lower or "v.douyin.com" in url_lower:
        return "douyin"
    if "weixin.qq.com/sph" in url_lower or "channels.weixin.qq.com" in url_lower:
        return "wechat_channels"
    return "unknown"


def is_browser_only_platform(url):
    return detect_platform(url) in ("xiaohongshu", "douyin", "bilibili")


def platform_zh_name(platform):
    return {
        "xiaohongshu": "小红书",
        "douyin": "抖音",
        "bilibili": "B 站",
        "youtube": "YouTube",
        "wechat_channels": "微信视频号",
        "local": "本地文件",
        "unknown": "未知平台",
    }.get(platform, platform or "视频")


def find_video_download_script():
    candidates = [
        os.path.join(os.path.expanduser("~"), ".workbuddy", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".agents", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".Codex", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".codex", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.expanduser("~"), ".claude", "skills", "video-download", "scripts", "download_video.py"),
        os.path.join(os.path.dirname(SKILL_DIR), "video-download", "scripts", "download_video.py"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def _run_video_download_json(args, timeout=900):
    script = find_video_download_script()
    if not script:
        raise RuntimeError("找不到 video-download/scripts/download_video.py")
    cmd = ["python3", script] + args + ["--json"]
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
    resolver = os.getenv("VIDEO_DOWNLOAD_WECHAT_RESOLVER")
    if resolver:
        args += ["--wechat-resolver", resolver]
    data = _run_video_download_json(args, timeout=1200)
    path = data.get("path")
    if not path or not os.path.exists(path):
        raise RuntimeError("video-download 未返回有效本地视频路径")
    return path, data.get("title") or ""


def get_video_info(video_path):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", video_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] 无法读取视频信息: {video_path}", file=sys.stderr)
        sys.exit(1)
    info = json.loads(result.stdout)
    duration = float(info.get("format", {}).get("duration", 0))
    width = height = 0
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width", 0)
            height = stream.get("height", 0)
            break
    return {
        "duration": round(duration, 1),
        "width": width,
        "height": height,
        "file_size_mb": round(os.path.getsize(video_path) / 1024 / 1024, 1),
        "file_name": os.path.basename(video_path),
    }


def wav_duration(wav_path):
    try:
        import wave
        with wave.open(wav_path, "r") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def download_video(url, output_dir=None):
    output_dir = output_dir or WORK_DIR
    os.makedirs(output_dir, exist_ok=True)
    for f in Path(output_dir).glob("*.mp4"):
        f.unlink()
    output_template = os.path.join(output_dir, "%(title).50s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        "--no-playlist",
        url,
    ]
    print(f"[INFO] 正在下载视频: {url}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败: {result.stderr[-400:]}")
    files = sorted(Path(output_dir).glob("*.mp4"), key=os.path.getmtime, reverse=True)
    if not files:
        for ext in ["*.webm", "*.mkv", "*.flv"]:
            files = sorted(Path(output_dir).glob(ext), key=os.path.getmtime, reverse=True)
            if files:
                break
    if not files:
        raise RuntimeError("yt-dlp 下载完成但找不到视频文件")
    output_path = str(files[0])
    print(f"[OK] 下载完成: {os.path.basename(output_path)}", file=sys.stderr)
    return output_path


def _curl_download(url, out_path, headers=None, timeout=900):
    cmd = ["curl", "-L", "-sS", "--fail", "-o", out_path]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(f"curl 下载失败: {r.stderr[-300:] or r.stdout[-300:]}")


def download_via_browser(url, output_dir=None, cached_info=None):
    output_dir = output_dir or WORK_DIR
    os.makedirs(output_dir, exist_ok=True)
    if cached_info:
        info = cached_info
        print("[INFO] 复用探测阶段的直链(无需重启浏览器)", file=sys.stderr)
    else:
        from platform_extractor import extract as platform_extract
        pname = detect_platform(url)
        print(f"[INFO] {platform_zh_name(pname)}链接,启动后台浏览器提取直链...", file=sys.stderr)
        info = platform_extract(url, headless=True)
    out_path = os.path.join(output_dir, "video.mp4")
    if os.path.exists(out_path):
        os.remove(out_path)
    if info.get("needs_merge"):
        v_path = os.path.join(output_dir, "_video.m4s")
        a_path = os.path.join(output_dir, "_audio.m4s")
        for p in (v_path, a_path):
            if os.path.exists(p):
                os.remove(p)
        _curl_download(info["video_url"], v_path, info.get("headers"))
        _curl_download(info["audio_url"], a_path, info.get("headers"))
        merge_cmd = ["ffmpeg", "-y", "-i", v_path, "-i", a_path, "-c", "copy", "-movflags", "+faststart", out_path]
        r = subprocess.run(merge_cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not os.path.exists(out_path):
            print(f"[ERROR] ffmpeg 合并失败: {r.stderr[-500:]}", file=sys.stderr)
            sys.exit(1)
    else:
        _curl_download(info["video_url"], out_path, info.get("headers"))
    return out_path, info.get("title") or ""


def extract_audio_wav(video_path, wav_path, start=None, end=None):
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", video_path]
    if end is not None and start is not None:
        cmd += ["-t", str(end - start)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
        raise RuntimeError(f"提取音频失败: {r.stderr[-300:]}")
    return wav_path


def extract_audio_from_url(url, wav_path, headers=None, timeout=900):
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    if headers:
        hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() != "user-agent")
        if hdr:
            cmd += ["-headers", hdr]
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            cmd += ["-user_agent", ua]
    cmd += ["-i", url, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path]
    print(f"[INFO] 直链提音频(跳过完整 MP4)...", file=sys.stderr)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
        raise RuntimeError(f"直链提音频失败: {(r.stderr or '')[-400:]}")
    return wav_path


def download_audio_ytdlp(url, wav_path):
    tmp_dir = os.path.dirname(wav_path) or WORK_DIR
    os.makedirs(tmp_dir, exist_ok=True)
    template = os.path.join(tmp_dir, "ytdlp_audio.%(ext)s")
    cmd = [
        "yt-dlp", "-f", "bestaudio/best",
        "-x", "--audio-format", "wav",
        "--postprocessor-args", "ffmpeg:-ac 1 -ar 16000",
        "-o", template, "--no-playlist", url,
    ]
    print("[INFO] yt-dlp 仅下载音频...", file=sys.stderr)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp 音频下载失败: {r.stderr[-400:]}")
    found = sorted(Path(tmp_dir).glob("ytdlp_audio.*"), key=os.path.getmtime, reverse=True)
    if not found:
        raise RuntimeError("yt-dlp 未产出音频文件")
    src = str(found[0])
    if src.endswith(".wav"):
        os.replace(src, wav_path)
    else:
        extract_audio_wav(src, wav_path)
    return wav_path


def transcribe_funasr(wav_path, language=None, hotword=None):
    from asr_daemon import _load_model, transcribe_with_model
    model = _load_model()
    return transcribe_with_model(model, wav_path, hotword=hotword)


def _fmt_mmss(sec):
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def segments_to_markdown(segments):
    from preorganize import cluster_segments, sentences_from_texts, gen_section_title, extract_keywords
    if not segments:
        return "_(未识别到语音)_"
    parts = []
    for i, (st, en, texts) in enumerate(cluster_segments(segments), 1):
        joined = "".join(texts)
        title = gen_section_title(joined, extract_keywords(joined, 2))
        header = f"## {i}. {title} [{_fmt_mmss(int(st))} - {_fmt_mmss(int(en))}]"
        lines = sentences_from_texts(texts)
        parts.append(f"{header}\n\n" + "\n".join(lines))
    return "\n\n".join(parts)


def estimate_local_time(duration):
    if not duration or duration <= 0:
        return None, 1
    n_segs = 1 if duration < 180 else max(2, int((duration + 299) // 300))
    return int(duration / 8) + 15, n_segs


def _ytdlp_probe(url):
    cmd = ["yt-dlp", "--dump-json", "--no-warnings", "--skip-download", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp probe 失败: {r.stderr[-300:]}")
    info = json.loads(r.stdout.split("\n")[0])
    return {
        "platform": detect_platform(url),
        "title": info.get("title"),
        "duration": int(info.get("duration") or 0),
        "needs_merge": False,
        "cached_info": None,
        "direct_url": None,
        "headers": None,
    }


def probe_video(input_path):
    if is_url(input_path):
        platform = detect_platform(input_path)
        if platform in ("xiaohongshu", "douyin", "bilibili"):
            from platform_extractor import extract as platform_extract
            info = platform_extract(input_path, headless=True)
            return {
                "platform": platform,
                "title": info.get("title") or "",
                "duration": int(info.get("duration") or 0),
                "cached_info": info,
                "direct_url": info.get("audio_url") or info.get("video_url"),
                "headers": info.get("headers"),
            }
        return _ytdlp_probe(input_path)
    meta = get_video_info(input_path)
    return {
        "platform": "local",
        "title": Path(input_path).stem,
        "duration": int(meta["duration"]),
        "cached_info": None,
        "direct_url": None,
        "headers": None,
    }


def resolve_or_probe(input_path):
    if is_url(input_path) and detect_platform(input_path) == "wechat_channels":
        from sph_resolver import resolve_wechat
        profile = resolve_wechat(input_path)
        return {
            "platform": "wechat_channels",
            "title": profile.get("title") or "",
            "duration": int(profile.get("duration") or 0),
            "direct_url": profile.get("direct_url"),
            "headers": {
                "User-Agent": WECHAT_UA,
                "Referer": "https://channels.weixin.qq.com/",
            },
            "cached_info": None,
            "author": profile.get("author"),
            "resolver": profile.get("resolver"),
        }
    return probe_video(input_path)


def fmt_duration_human(sec):
    if not sec or sec <= 0:
        return "未知"
    sec = int(sec)
    if sec < 60:
        return f"{sec}秒"
    m, s = sec // 60, sec % 60
    if sec < 3600:
        return f"{m}分{s:02d}秒"
    h, m = m // 60, m % 60
    return f"{h}小时{m:02d}分"


def fmt_estimate_range(sec):
    if not sec:
        return "未知"
    return f"{fmt_duration_human(int(sec * 0.8))} ~ {fmt_duration_human(int(sec * 1.3))}"


def print_probe_report(meta, est_sec, n_segs):
    bar = "═" * 55
    sep = "─" * 55
    print(bar, file=sys.stderr)
    print("  📊 视频探测", file=sys.stderr)
    print(sep, file=sys.stderr)
    print(f"  平台:      {platform_zh_name(meta.get('platform'))}", file=sys.stderr)
    print(f"  标题:      {meta.get('title') or '(未抓到标题)'}", file=sys.stderr)
    print(f"  时长:      {fmt_duration_human(meta.get('duration') or 0)}", file=sys.stderr)
    if n_segs > 1:
        print(f"  分段:      {n_segs} 段并行/流式转录(每段 ≤ 5 分钟)", file=sys.stderr)
    else:
        print("  分段:      1 段(短视频整体处理)", file=sys.stderr)
    if est_sec:
        print(f"  预估耗时:  {fmt_estimate_range(est_sec)}", file=sys.stderr)
    print(bar, file=sys.stderr)


def safe_filename(name, max_len=60):
    name = re.sub(r'[\\/:*?"<>|]', "_", name or "").strip()
    return name[:max_len] or "transcript"


def normalize_input(value):
    if not is_url(value):
        return os.path.abspath(os.path.expanduser(value))
    m = re.search(r"/sph/([A-Za-z0-9_-]+)", value)
    if m:
        return f"https://weixin.qq.com/sph/{m.group(1)}"
    if "channels.weixin.qq.com" in value:
        from urllib.parse import parse_qs, urlparse
        sid = (parse_qs(urlparse(value).query).get("id") or [""])[0]
        if sid:
            return f"https://weixin.qq.com/sph/{sid}"
    return value.split("#")[0].rstrip("/")


def cache_key(value):
    return hashlib.sha1(normalize_input(value).encode("utf-8")).hexdigest()[:16]


def _load_cache_index():
    if not os.path.exists(CACHE_INDEX):
        return {}
    try:
        with open(CACHE_INDEX, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def cache_lookup(input_path):
    hit = _load_cache_index().get(cache_key(input_path))
    if not hit:
        return None
    pre = hit.get("preorganized_path")
    if pre and os.path.exists(pre):
        return hit
    return None


def cache_store(input_path, payload):
    os.makedirs(os.path.dirname(CACHE_INDEX), exist_ok=True)
    idx = _load_cache_index()
    idx[cache_key(input_path)] = payload
    with open(CACHE_INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def build_toc(md):
    entries = re.findall(
        r"^## (\d+)\.\s*(.+?)\s*\[(\d+):(\d+)\s*-\s*(\d+):(\d+)\]", md, re.M
    )
    if len(entries) <= 3:
        return ""
    lines = ["## 目录", ""]
    for num, title, sm, ss, em, es in entries:
        lines.append(f"{num}. {title} [{sm}:{ss}]")
    return "\n".join(lines) + "\n\n"


def md_to_html(md_text, download_name="transcript.md"):
    try:
        import markdown as _md
    except ImportError:
        return None
    body_html = _md.markdown(md_text, extensions=["extra"])
    md_json = json.dumps(md_text).replace("</", "<\\/")
    title = "视频逐字稿"
    for line in md_text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            break
    html_title = json.dumps(title)[1:-1]
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html_title}</title></head>
<body>
<pre style="display:none" id="md"></pre>
<article>{body_html}</article>
<script>const MD={md_json};</script>
</body></html>"""


def kickoff_asr_daemon(use_daemon):
    if not use_daemon:
        return
    try:
        from asr_daemon import start_background
        start_background()
        print("[INFO] FunASR daemon 已在后台预热(与提音频并行)", file=sys.stderr)
    except Exception as exc:
        print(f"[WARN] daemon 启动失败,将进程内加载: {exc}", file=sys.stderr)


def split_wav_chunks(wav_path, duration, chunk_sec=300):
    if not duration or duration < 180:
        return [(wav_path, 0.0)]
    work = os.path.join(os.path.dirname(wav_path) or WORK_DIR, "chunks")
    os.makedirs(work, exist_ok=True)
    chunks = []
    start = 0.0
    idx = 0
    while start < duration - 1:
        end = min(duration, start + chunk_sec)
        out = os.path.join(work, f"chunk_{idx:02d}.wav")
        extract_audio_wav(wav_path, out, start=start, end=end)
        chunks.append((out, start))
        start = end
        idx += 1
    return chunks or [(wav_path, 0.0)]


def write_stream_chunk(stream_dir, idx, total, segments):
    if not stream_dir:
        return
    os.makedirs(stream_dir, exist_ok=True)
    payload = {"index": idx, "total": total, "segments": segments}
    json_path = os.path.join(stream_dir, f"chunk_{idx:02d}.json")
    md_path = os.path.join(stream_dir, f"chunk_{idx:02d}.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(segments_to_markdown(segments))
    with open(os.path.join(stream_dir, "progress.json"), "w", encoding="utf-8") as f:
        json.dump({"done": idx + 1, "total": total, "latest_md": md_path}, f, ensure_ascii=False)
    print(f"[STREAM] chunk {idx + 1}/{total} ready: {md_path}", file=sys.stderr)


def _proc_transcribe(job):
    path, offset, hotword, idx = job
    scripts = os.path.dirname(os.path.abspath(__file__))
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from asr_daemon import _load_model, transcribe_with_model
    model = _load_model()
    segs = transcribe_with_model(model, path, hotword=hotword)
    shifted = []
    for s in segs:
        item = dict(s)
        item["start"] = round(float(item.get("start") or 0) + offset, 2)
        item["end"] = round(float(item.get("end") or 0) + offset, 2)
        shifted.append(item)
    return idx, shifted


def transcribe_smart(wav_path, duration, hotword, stream_dir, use_daemon=True):
    chunks = split_wav_chunks(wav_path, duration)
    total = len(chunks)
    daemon_ok = False
    if use_daemon:
        try:
            from asr_daemon import ping, wait_until_ready, transcribe_via_daemon
            info = ping(timeout=2)
            if not (info and info.get("ready")):
                print("[INFO] 等待 FunASR 模型就绪...", file=sys.stderr)
                info = wait_until_ready(90)
            daemon_ok = bool(info and info.get("ready"))
        except Exception as exc:
            print(f"[WARN] daemon 不可用: {exc}", file=sys.stderr)

    all_segs = []
    if daemon_ok:
        from asr_daemon import transcribe_via_daemon
        for i, (path, offset) in enumerate(chunks):
            segs = transcribe_via_daemon(path, hotword=hotword, offset=offset)
            write_stream_chunk(stream_dir, i, total, segs)
            all_segs.extend(segs)
        return all_segs

    if total >= 2:
        print(f"[INFO] 分块并行转录 {total} 段 × 最多 2 进程", file=sys.stderr)
        jobs = [(path, offset, hotword, i) for i, (path, offset) in enumerate(chunks)]
        results = [None] * total
        with ProcessPoolExecutor(max_workers=min(2, total)) as ex:
            futs = {ex.submit(_proc_transcribe, job): job[3] for job in jobs}
            for fut in as_completed(futs):
                idx, segs = fut.result()
                results[idx] = segs
                write_stream_chunk(stream_dir, idx, total, segs)
        for segs in results:
            all_segs.extend(segs or [])
        all_segs.sort(key=lambda s: float(s.get("start") or 0))
        return all_segs

    segs = transcribe_funasr(wav_path, hotword=hotword)
    write_stream_chunk(stream_dir, 0, 1, segs)
    return segs


def pick_audio_source(meta):
    cached = meta.get("cached_info") or {}
    url = meta.get("direct_url") or cached.get("audio_url") or cached.get("video_url")
    headers = meta.get("headers") or cached.get("headers")
    return url, headers


def acquire_wav(input_path, meta, wav_path, keep_video=False):
    if not is_url(input_path):
        if os.path.abspath(input_path) == os.path.abspath(wav_path):
            return wav_path, input_path
        extract_audio_wav(input_path, wav_path)
        return wav_path, input_path

    audio_url, headers = pick_audio_source(meta)
    video_path = None
    if audio_url:
        try:
            extract_audio_from_url(audio_url, wav_path, headers=headers)
        except RuntimeError as exc:
            print(f"[WARN] 直链提音频失败,回退下载: {exc}", file=sys.stderr)
            audio_url = None
    if not audio_url and os.path.exists(wav_path) and os.path.getsize(wav_path) >= 1024:
        pass
    elif not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
        platform = meta.get("platform")
        if platform == "youtube" or (platform == "unknown" and check_ytdlp()):
            try:
                download_audio_ytdlp(input_path, wav_path)
            except RuntimeError as exc:
                print(f"[WARN] yt-dlp 音频失败,回退完整下载: {exc}", file=sys.stderr)
                video_path = download_video(input_path)
                extract_audio_wav(video_path, wav_path)
        elif platform == "wechat_channels":
            video_path, _ = download_via_video_download(input_path)
            extract_audio_wav(video_path, wav_path)
        elif is_browser_only_platform(input_path):
            video_path, _ = download_via_browser(input_path, cached_info=meta.get("cached_info"))
            extract_audio_wav(video_path, wav_path)
        else:
            video_path = download_video(input_path)
            extract_audio_wav(video_path, wav_path)

    if keep_video and not video_path:
        print("[INFO] --keep-video: 额外保存 MP4", file=sys.stderr)
        try:
            if meta.get("platform") == "wechat_channels":
                video_path, _ = download_via_video_download(input_path)
            elif is_browser_only_platform(input_path):
                video_path, _ = download_via_browser(input_path, cached_info=meta.get("cached_info"))
            else:
                video_path = download_video(input_path)
        except Exception as exc:
            print(f"[WARN] 保存 MP4 失败(不影响转录): {exc}", file=sys.stderr)
    return wav_path, video_path


def emit_cache_hit(hit):
    print("[OK] 缓存命中,跳过下载/转录", file=sys.stderr)
    print(f"  预整理: {hit.get('preorganized_path')}", file=sys.stderr)
    if hit.get("transcript_path"):
        print(f"  原始稿: {hit.get('transcript_path')}", file=sys.stderr)
    pre = hit.get("preorganized_path")
    if pre and os.path.exists(pre):
        with open(pre, encoding="utf-8") as f:
            print(f.read())
    print("----- VT_OUTPUTS -----", file=sys.stderr)
    print(json.dumps({"cache": True, **hit}, ensure_ascii=False), file=sys.stderr)


def run(input_path, title=None, output_dir=None, save_md=True, use_cache=True, keep_video=False, use_daemon=True):
    if not check_ffmpeg():
        print("[ERROR] ffmpeg 未安装!请运行: brew install ffmpeg", file=sys.stderr)
        sys.exit(1)

    if use_cache and cache_lookup(input_path):
        emit_cache_hit(cache_lookup(input_path))
        return

    kickoff_asr_daemon(use_daemon)

    print("[Step 0/3] 解析视频(只跑一次)...", file=sys.stderr)
    try:
        meta = resolve_or_probe(input_path)
    except Exception as e:
        print(f"[ERROR] 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not title and meta.get("title"):
        title = meta["title"]

    est_sec, n_segs = estimate_local_time(meta.get("duration", 0))
    print_probe_report(meta, est_sec, n_segs)

    os.makedirs(WORK_DIR, exist_ok=True)
    wav_path = os.path.join(WORK_DIR, "audio.wav")
    if os.path.exists(wav_path):
        try:
            os.remove(wav_path)
        except OSError:
            pass

    print("\n[Step 1/3] 提取 16k 单声道音频...", file=sys.stderr)
    try:
        wav_path, video_path = acquire_wav(input_path, meta, wav_path, keep_video=keep_video)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    duration = meta.get("duration") or 0
    wav_dur = wav_duration(wav_path)
    if wav_dur > 0:
        duration = wav_dur
        meta["duration"] = int(wav_dur)
    print(f"[INFO] 音频 {os.path.getsize(wav_path)/1024/1024:.1f}MB / {fmt_duration_human(duration)}", file=sys.stderr)

    out_dir = output_dir or DEFAULT_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    name_seed = title or Path(wav_path).stem
    date_prefix = time.strftime("%Y-%m-%d")
    stem = f"{date_prefix}_{safe_filename(name_seed, 30)}"
    stream_dir = os.path.join(out_dir, ".partial", cache_key(input_path))

    print("\n[Step 2/3] FunASR 转录...", file=sys.stderr)
    try:
        segments = transcribe_smart(
            wav_path, duration, FUNASR_HOTWORD, stream_dir, use_daemon=use_daemon
        )
    except Exception as e:
        print(f"[ERROR] FunASR 转录失败: {e}", file=sys.stderr)
        print("  提示: 首次使用需联网下载模型(约 234M)。", file=sys.stderr)
        sys.exit(1)

    transcript_md = segments_to_markdown(segments)
    platform = meta.get("platform") or "unknown"
    source = input_path if is_url(input_path) else os.path.basename(input_path)
    gen_date = time.strftime("%Y-%m-%d %H:%M")
    header = ""
    if title:
        link_part = f" | 链接: {input_path}" if is_url(input_path) else ""
        header = (
            f"# {title}\n\n"
            f"> 来源: {platform_zh_name(platform)}{link_part} | "
            f"时长 {_fmt_mmss(int(duration))} | 引擎: FunASR(SenseVoice-Small) | 生成: {gen_date}\n\n"
        )
    raw_md = header + build_toc(transcript_md) + transcript_md

    from preorganize import build_sections, render_preorganized_md, build_polish_brief, write_json, detect_suspects
    sections = build_sections(segments)
    pre_title = title or name_seed
    pre_md = render_preorganized_md(
        pre_title,
        {
            "source": platform_zh_name(platform),
            "url": input_path if is_url(input_path) else "",
            "duration_label": _fmt_mmss(int(duration)),
            "transcribed_at": gen_date,
        },
        sections,
    )
    brief = build_polish_brief(pre_title, sections, detect_suspects(pre_md))

    outputs = {}
    if save_md:
        raw_file = os.path.join(out_dir, f"{stem}_transcript.md")
        pre_file = os.path.join(out_dir, f"{stem}_预整理.md")
        brief_file = os.path.join(out_dir, f"{stem}_polish_brief.json")
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(raw_md)
        with open(pre_file, "w", encoding="utf-8") as f:
            f.write(pre_md)
        write_json(brief_file, brief)
        print(f"\n[OK] 原始稿: {raw_file}", file=sys.stderr)
        print(f"[OK] 预整理: {pre_file}", file=sys.stderr)
        print(f"[OK] 润色 brief: {brief_file}", file=sys.stderr)
        outputs = {
            "transcript_path": raw_file,
            "preorganized_path": pre_file,
            "polish_brief_path": brief_file,
            "stream_dir": stream_dir if os.path.isdir(stream_dir) else None,
            "title": pre_title,
            "duration": int(duration),
            "created_at": gen_date,
            "source_url": normalize_input(input_path),
        }
        sidecar = os.path.join(out_dir, f"{stem}_outputs.json")
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(outputs, f, ensure_ascii=False, indent=2)
        outputs["outputs_json"] = sidecar
        cache_store(input_path, outputs)
        try:
            html_str = md_to_html(raw_md, download_name=os.path.basename(raw_file))
            if html_str:
                html_file = os.path.splitext(raw_file)[0] + ".html"
                with open(html_file, "w", encoding="utf-8") as f:
                    f.write(html_str)
        except Exception:
            pass

    print("=" * 55, file=sys.stderr)
    print("[OK] 转录+预整理完成。agent 请读预整理稿,只产出 patch,不要重写全文。", file=sys.stderr)
    print(pre_md)
    print("----- VT_OUTPUTS -----", file=sys.stderr)
    print(json.dumps(outputs, ensure_ascii=False), file=sys.stderr)


def doctor():
    print("=" * 55)
    print("  🩺 video-transcript 体检")
    print("=" * 55)
    issues = []
    if check_ffmpeg():
        print("  ✓ ffmpeg")
    else:
        print("  ✗ ffmpeg 未安装")
        issues.append("brew install ffmpeg")
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
        print("  ✓ ffprobe")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("  ✗ ffprobe 未安装")
        issues.append("brew install ffmpeg")
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(f"  {'✓' if sys.version_info >= (3, 8) else '✗'} Python {py}")
    if check_ytdlp():
        print("  ✓ yt-dlp")
    else:
        print("  ⚠ yt-dlp 未安装(YouTube 会受影响)")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and os.path.exists(exe):
                print("  ✓ playwright + chromium")
            else:
                print("  ✗ chromium 没装")
                issues.append("python3 -m playwright install chromium")
    except ImportError:
        print("  ✗ playwright 未安装")
        issues.append("pip install playwright")
    try:
        import funasr
        print(f"  ✓ funasr({funasr.__version__})")
    except ImportError:
        print("  ✗ funasr 未安装")
        issues.append("pip install funasr torchaudio")
    if find_video_download_script():
        print(f"  ✓ video-download: {find_video_download_script()}")
    else:
        print("  ⚠ video-download 未安装(仅影响 --keep-video / 回退下载)")
    state = os.path.expanduser("~/.workbuddy/credentials/yuanbao_state.json")
    if os.path.exists(state):
        print(f"  ✓ 元宝登录态: {state}")
    else:
        print("  ⚠ 元宝登录态不存在(视频号需 sph_resolver.py --login)")
    try:
        from asr_daemon import ping
        info = ping(timeout=1)
        if info and info.get("ready"):
            print("  ✓ FunASR daemon 已就绪")
        elif info:
            print("  ⚠ FunASR daemon 在跑,模型加载中")
        else:
            print("  ⚠ FunASR daemon 未启动(首次转录会自动拉起)")
    except Exception:
        print("  ⚠ FunASR daemon 状态未知")
    print("=" * 55)
    if issues:
        print(f"  ❌ 发现 {len(issues)} 个问题:")
        for x in issues:
            print(f"     - {x}")
        return 1
    print("  ✅ 全部就绪")
    return 0


def main():
    parser = argparse.ArgumentParser(description="视频逐字稿提取(加速版:HTTP 解析 + 直链音频 + daemon + 预整理)")
    parser.add_argument("input", nargs="?", help="视频 URL 或本地文件路径")
    parser.add_argument("--title", default=None)
    parser.add_argument("--no-save", dest="save_md", action="store_false")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--no-cache", dest="use_cache", action="store_false")
    parser.add_argument("--force", action="store_true", help="忽略缓存,强制重跑")
    parser.add_argument("--keep-video", action="store_true", help="额外保存完整 MP4")
    parser.add_argument("--no-daemon", dest="use_daemon", action="store_false")
    parser.set_defaults(save_md=True, use_cache=True, use_daemon=True)
    args = parser.parse_args()
    if args.doctor:
        sys.exit(doctor())
    if not args.input:
        parser.error("缺少 input 参数")
    run(
        args.input,
        title=args.title,
        output_dir=args.output_dir,
        save_md=args.save_md,
        use_cache=(args.use_cache and not args.force),
        keep_video=args.keep_video,
        use_daemon=args.use_daemon,
    )


if __name__ == "__main__":
    main()
