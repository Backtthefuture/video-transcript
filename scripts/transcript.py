#!/usr/bin/env python3
"""
视频逐字稿提取工具
- 支持B站/YouTube/小红书/抖音/微信视频号链接 或 本地视频文件
- 下载 + 提音频 + FunASR(SenseVoice-Small)本地转录,全程离线
- 只输出"语义分段 + 段落级时间戳"的 Markdown 逐字稿
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import ssl
import urllib.request
import urllib.error
from pathlib import Path

# macOS Python SSL 证书修复(安全版:优先用 certifi 根证书,保持证书校验开启)
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

# ─── 配置 ───────────────────────────────────────────────
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT_DIR = os.path.join(SKILL_DIR, "outputs")
ENV_FILE = os.path.join(SKILL_DIR, ".env")


def _load_dotenv(path):
    """简单 .env 加载器:KEY=VALUE 格式,支持引号、注释、空行。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            # 去掉引号
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            # 不覆盖已有的环境变量(让 shell export 优先)
            os.environ.setdefault(k, v)


_load_dotenv(ENV_FILE)


# FunASR 引擎(SenseVoice-Small)配置
FUNASR_HOTWORD = os.getenv("FUNASR_HOTWORD") or None  # 热词,提升专有名词识别率
WORK_DIR = "/tmp/video-transcript"


# ─── 工具函数 ──────────────────────────────────────────

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
    return path.startswith("http://") or path.startswith("https://")


def detect_platform(url):
    url_lower = url.lower()
    if 'bilibili.com' in url_lower or 'b23.tv' in url_lower:
        return 'bilibili'
    elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'xiaohongshu.com' in url_lower or 'xhslink.com' in url_lower:
        return 'xiaohongshu'
    elif 'douyin.com' in url_lower or 'v.douyin.com' in url_lower:
        return 'douyin'
    elif 'weixin.qq.com/sph' in url_lower or 'channels.weixin.qq.com' in url_lower:
        return 'wechat_channels'
    return 'unknown'


def is_browser_only_platform(url):
    # B 站 yt-dlp 412 概率高,默认也走 headless;youtube 走 yt-dlp
    return detect_platform(url) in ('xiaohongshu', 'douyin', 'bilibili')


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
            err = (r.stderr or r.stdout or "").strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else ""
        raise RuntimeError(f"video-download 失败: {err or '未知错误'}")
    try:
        data = json.loads(r.stdout.strip())
    except json.JSONDecodeError:
        raise RuntimeError("video-download 未返回 JSON")
    if not data.get("ok"):
        raise RuntimeError(f"video-download 失败: {data.get('error') or '未知错误'}")
    return data


def probe_via_video_download(url):
    args = [url, "--probe"]
    resolver = os.getenv("VIDEO_DOWNLOAD_WECHAT_RESOLVER")
    if resolver:
        args += ["--wechat-resolver", resolver]
    data = _run_video_download_json(args, timeout=80)
    return {
        "platform": data.get("platform") or "wechat_channels",
        "title": data.get("title") or "",
        "duration": int(data.get("duration") or 0),
        "cached_info": None,
    }


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
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] 无法读取视频信息: {video_path}", file=sys.stderr)
        sys.exit(1)
    info = json.loads(result.stdout)

    duration = float(info.get("format", {}).get("duration", 0))
    width, height = 0, 0
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


# ─── Step 1: 下载视频 ─────────────────────────────────

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
        url
    ]

    print(f"[INFO] 正在下载视频: {url}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        # 让调用方决定是否回退到 headless 浏览器
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
    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"[OK] 下载完成: {os.path.basename(output_path)} ({size_mb:.1f}MB)", file=sys.stderr)
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
    """抖音/小红书/B站:用 Playwright headless 后台抓视频直链,再用 curl 下载。
    B 站走 dash 流(分别下载 video + audio m4s,再 ffmpeg 合并)。
    cached_info 由 probe 阶段提供,避免重复启动 headless。"""
    output_dir = output_dir or WORK_DIR
    os.makedirs(output_dir, exist_ok=True)

    if cached_info:
        info = cached_info
        print(f"[INFO] 复用探测阶段的直链(无需重启浏览器)", file=sys.stderr)
    else:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from platform_extractor import extract as platform_extract

        pname = detect_platform(url)
        pname_zh = {"douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "B 站"}.get(pname, pname)
        print(f"[INFO] {pname_zh}链接,启动后台浏览器提取直链(headless,无窗口)...", file=sys.stderr)
        info = platform_extract(url, headless=True)
        print(f"[OK] 标题: {info['title']}", file=sys.stderr)

    out_path = os.path.join(output_dir, "video.mp4")
    if os.path.exists(out_path):
        os.remove(out_path)

    if info.get("needs_merge"):
        # B 站 dash:分别下载 video.m4s + audio.m4s,再 ffmpeg copy 合并
        v_path = os.path.join(output_dir, "_video.m4s")
        a_path = os.path.join(output_dir, "_audio.m4s")
        for p in (v_path, a_path):
            if os.path.exists(p):
                os.remove(p)
        print(f"[INFO] 下载视频流...", file=sys.stderr)
        _curl_download(info["video_url"], v_path, info.get("headers"))
        print(f"[INFO] 下载音频流...", file=sys.stderr)
        _curl_download(info["audio_url"], a_path, info.get("headers"))
        print(f"[INFO] ffmpeg 合并 video + audio...", file=sys.stderr)
        merge_cmd = [
            "ffmpeg", "-y", "-i", v_path, "-i", a_path,
            "-c", "copy", "-movflags", "+faststart", out_path,
        ]
        r = subprocess.run(merge_cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not os.path.exists(out_path):
            print(f"[ERROR] ffmpeg 合并失败: {r.stderr[-500:]}", file=sys.stderr)
            sys.exit(1)
        for p in (v_path, a_path):
            try: os.remove(p)
            except OSError: pass
    else:
        print(f"[INFO] 下载视频...", file=sys.stderr)
        try:
            _curl_download(info["video_url"], out_path, info.get("headers"))
        except RuntimeError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"[OK] 下载完成: {os.path.basename(out_path)} ({size_mb:.1f}MB)", file=sys.stderr)
    return out_path, info["title"]


# ─── Step 2: 视频压缩 ─────────────────────────────────









# ─── Step 3: 本地转录引擎(FunASR) ──────────────────────









# ─── 本地引擎(FunASR SenseVoice-Small) ─────────────────

def extract_audio_wav(video_path, wav_path, start=None, end=None):
    """从视频(或切片)提取 16k 单声道 wav,供本地 ASR 转录。"""
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-y"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", video_path]
    if end is not None:
        cmd += ["-t", str(end - start)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
        raise RuntimeError(f"提取音频失败: {r.stderr[-300:]}")
    return wav_path




def transcribe_funasr(wav_path, language=None, hotword=None):
    """调用 FunASR SenseVoice-Small 转录(CPU,中文最优)。返回 segments list。

    - 非自回归模型,中文 CER 7.81%(vs Whisper 20%),CPU 17x 实时
    - 内置 VAD(fsmn-vad)+ 标点(punc),自带中文标点
    - 首次运行自动下载模型(~234M,远小于 Whisper 1.5GB)
    - SenseVoice 返回整段文本(无时间戳),这里按句号切句 + 按字数比例估算时间戳
    - 返回结构: [{"start":.., "end":.., "text":..}]
    """
    try:
        from funasr import AutoModel
    except ImportError:
        print("[ERROR] FunASR 引擎需要 funasr。", file=sys.stderr)
        print("  安装: pip install funasr torchaudio", file=sys.stderr)
        sys.exit(1)

    # 获取音频总时长(用于估算时间戳)
    duration = 0.0
    try:
        import wave
        with wave.open(wav_path, "r") as wf:
            duration = wf.getnframes() / float(wf.getframerate())
    except Exception:
        pass

    print("[INFO] 首次使用需下载 SenseVoice-Small 模型(约 234M)...", file=sys.stderr)
    model_kwargs = dict(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        punc_model="ct-punc-c",
        device="cpu",
        disable_update=True,
    )
    model = AutoModel(**model_kwargs)

    gen_kwargs = dict(
        input=wav_path,
        batch_size_s=300,
    )
    if hotword:
        gen_kwargs["hotword"] = hotword

    res = model.generate(**gen_kwargs)

    if not res:
        return []

    raw_text = res[0].get("text", "").strip()
    if not raw_text:
        return []

    # SenseVoice 返回整段带标点文本,无时间戳
    # 按句号/问号/感叹号切句,保留标点
    sentences = re.split(r'(?<=[。！？!?…])', raw_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return [{"start": 0.0, "end": duration, "text": raw_text}]

    # 按字数比例估算每句时间戳
    total_chars = sum(len(s) for s in sentences)
    segments = []
    char_offset = 0
    for sent in sentences:
        ratio = char_offset / total_chars if total_chars > 0 else 0
        next_ratio = (char_offset + len(sent)) / total_chars if total_chars > 0 else 1
        start = ratio * duration if duration > 0 else 0
        end = next_ratio * duration if duration > 0 else 0
        segments.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": sent,
        })
        char_offset += len(sent)

    return segments


# ─── 格式优化:分句 / 关键词 / 语义标题 / 目录 ──────────

# 精简中文停用词(口语弱词 + 虚词)
STOPWORDS = set("""的 了 是 我 你 他 她 它 我们 你们 他们 咱们 这个 那个 一个 什么 怎么 就是 然后 但是 因为 所以 如果 而且 自己 现在 已经 非常 真的 觉得 认为 知道 时候 大家 一些 这种 那样 其实 还是 可以 没有 不是 应该 所有 以及 或者 等等 对于 关于 通过 从 到 在 有 和 与 就 都 也 很 太 更 最 不 没 吗 呢 吧 啊 呀 哦 嗯 那 这 让 把 被 给 向 为 之 其 它 说 讲 看 要 会 能 着 过 得 地 个 之 又 再 还 才 只 但 而 或 及 若 与""".split())

_WEAK_LEAD = ("我觉得", "我认为", "就是说", "就是说呢", "就是", "然后", "那么", "其实", "所以", "这个", "那个")


def split_sentences(text):
    """按中文标点拆句(保留标点),每句一行。"""
    parts = re.split(r"(?<=[。！？…!?])", text)
    return [p.strip() for p in parts if p.strip()]


# 口语连词:用于把无标点长句切成子句(分行;只按连词切,避免代词切得过碎)
_CLAUSE_RE = re.compile(r"(?=(?:然后|但是|但|所以|因为|就是|如果|要是|而且|其实|不过|因此|那|这))")
# 疑问标记词
_QWORDS = ("什么", "怎么", "为什么", "哪里", "哪儿", "谁", "吗", "呢", "么", "咋", "如何", "是不是", "要不要", "能不能", "有没有", "对不对", "好不好")


def add_sentence_punct(text):
    """给无标点的口语文本补句尾标点(规则法,不改字):疑问句→? 其余→。"""
    t = text.rstrip()
    if not t:
        return text
    if t[-1] in "。！？…，、；：,.!?;:":
        return t
    if any(w in t for w in _QWORDS):
        return t + "？"
    return t + "。"


def split_clauses(text):
    """把无标点长文本按口语连词切成子句(供分行;过短碎片向后合并)。"""
    parts = [p for p in _CLAUSE_RE.split(text) if p]
    merged = []
    for p in parts:
        if merged and len(merged[-1]) < 12:
            merged[-1] += p
        else:
            merged.append(p)
    return [p for p in merged if p.strip()]


def extract_keywords(text, top_n=2):
    """jieba 分词 + 停用词过滤,取段内高频实词。jieba 缺失时返回空(标题自动降级)。"""
    try:
        import jieba
    except ImportError:
        return []
    words = [w for w in jieba.cut(text)
             if len(w) >= 2 and w not in STOPWORDS and not w.isdigit()]
    from collections import Counter
    return [w for w, _ in Counter(words).most_common(top_n)]


def _clean_lead(s):
    """去掉句首口语弱词("我觉得/就是/那..."等),让标题更有信息量。"""
    for w in _WEAK_LEAD:
        if s.startswith(w) and len(s) > len(w) + 4:
            return s[len(w):]
    return s


def gen_section_title(text, keywords):
    """规则法生成段落小标题: 有效首句截断 ≤14 字; 太短则拼关键词。"""
    sents = split_sentences(text)
    base = ""
    for s in sents:
        t = _clean_lead(s).strip(" ，。！？…:：")
        if len(t) >= 6:
            base = t
            break
    if not base:
        base = sents[0][:14] if sents else ""
    base = base[:14]
    if keywords:
        kw = " · ".join(keywords[:2])
        if len(base) < 8 and kw:
            return f"{base}｜{kw}"[:20] if base else kw
    return base or "段落"


def md_to_html(md_text, download_name="transcript.md"):
    """把逐字稿 markdown 转成自包含 HTML 预览版(顶部带"复制全文 / 下载 .md"按钮)。
    返回完整 html 字符串。markdown 库缺失时返回 None(不影响 .md 落盘)。"""
    try:
        import markdown as _md
    except ImportError:
        return None
    import json as _json
    body_html = _md.markdown(md_text, extensions=["extra"])
    md_json = _json.dumps(md_text).replace("</", "<\\/")  # JSON 转义,防 script 截断
    # 标题:取第一个 # 行
    title = "视频逐字稿"
    for line in md_text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            break
    html_title = _json.dumps(title)[1:-1]
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_title}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; background: #f7f7f5; color: #1f2328; line-height: 1.75; }}
  .toolbar {{ position: sticky; top: 0; z-index: 10; display: flex; gap: 10px; justify-content: center; padding: 14px; background: rgba(247,247,245,.96); backdrop-filter: blur(6px); border-bottom: 1px solid #e6e6e3; }}
  .toolbar button {{ border: 1px solid #d0d0cc; background: #fff; color: #1f2328; border-radius: 8px; padding: 8px 18px; font-size: 14px; cursor: pointer; transition: all .15s; }}
  .toolbar button:hover {{ background: #f0f0ee; border-color: #b8b8b3; }}
  .toolbar button:active {{ transform: translateY(1px); }}
  .toolbar button.primary {{ background: #1f2328; color: #fff; border-color: #1f2328; }}
  .toolbar button.primary:hover {{ background: #33383f; }}
  article {{ max-width: 760px; margin: 0 auto; padding: 36px 28px 80px; background: #fff; min-height: 100vh; }}
  article h1 {{ font-size: 26px; font-weight: 600; line-height: 1.4; margin: 0 0 12px; }}
  article h2 {{ font-size: 19px; font-weight: 600; margin: 36px 0 10px; padding-top: 24px; border-top: 1px solid #eee; }}
  article h2:first-of-type {{ border-top: none; padding-top: 0; }}
  article p {{ margin: 12px 0; font-size: 16px; }}
  article blockquote {{ margin: 14px 0; padding: 10px 16px; border-left: 3px solid #d0d0cc; background: #fafaf8; color: #57606a; font-size: 14px; border-radius: 0 8px 8px 0; }}
  article blockquote p {{ margin: 4px 0; font-size: 14px; }}
  article ol, article ul {{ margin: 12px 0 12px 26px; }}
  article li {{ margin: 4px 0; font-size: 15px; }}
  article hr {{ border: none; border-top: 1px solid #eee; margin: 28px 0; }}
  article code {{ background: #f2f2f0; padding: 2px 6px; border-radius: 4px; font-size: 14px; }}
  @media (max-width: 640px) {{ article {{ padding: 24px 18px 60px; }} article h1 {{ font-size: 22px; }} article h2 {{ font-size: 17px; }} article p {{ font-size: 15px; }} }}
</style>
</head>
<body>
<div class="toolbar">
  <button class="primary" onclick="copyMd()">复制全文</button>
  <button onclick="downloadMd()">下载 .md</button>
</div>
<article>
{body_html}
</article>
<script>
const MD = {md_json};
const FN = {_json.dumps(download_name)};
async function copyMd(){{
  try {{
    await navigator.clipboard.writeText(MD);
    flash("已复制全文");
  }} catch (e) {{
    const ta = document.createElement("textarea");
    ta.value = MD; document.body.appendChild(ta); ta.select();
    try {{ document.execCommand("copy"); flash("已复制全文"); }}
    catch (e2) {{ flash("复制失败,请手动选择复制"); }}
    document.body.removeChild(ta);
  }}
}}
function downloadMd(){{
  const blob = new Blob([MD], {{ type: "text/markdown" }});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = FN; a.click();
  URL.revokeObjectURL(a.href);
}}
function flash(msg){{
  const d = document.createElement("div");
  d.textContent = msg;
  d.style.cssText = "position:fixed;left:50%;top:64px;transform:translateX(-50%);background:#1f2328;color:#fff;padding:8px 18px;border-radius:8px;font-size:13px;z-index:99;";
  document.body.appendChild(d);
  setTimeout(() => d.remove(), 1600);
}}
</script>
</body>
</html>"""


def build_toc(md):
    """段数 > 3 时生成目录(标题 + 起始时间戳),长视频导航用。"""
    entries = re.findall(r"^## (\d+)\.\s*(.+?)\s*\[(\d+):(\d+)\s*-\s*(\d+):(\d+)\]", md, re.M)
    if len(entries) <= 3:
        return ""
    lines = ["## 目录", ""]
    for num, title, sm, ss, em, es in entries:
        lines.append(f"{num}. {title} [{sm}:{ss}]")
    return "\n".join(lines) + "\n\n"


# ─── 口述话题转折检测(改善 60s 聚类的机械切分) ─────────
# 背景:60s 聚类会把口述"第 N 个问题/点"的完整论述拦腰切断(如
# "第五个就是……奢华无边界"与其延续"个人成功→佣金预判"分到两段)。
# 规则:segment 开头 lookahead 字符内出现话题编号/转折标记时,无条件优先在此切段,
#      保证口述话题不被切成两半;无标记时保持原有 60s 节奏。
TOPIC_MARKER_RE = re.compile(
    r'第[一二三四五六七八九十百\d]+[个点]'
    r'|还有一个问题|另外一个|接下来|再说一遍|总结一下'
    r'|最后[一句话讲]?'
)


def _is_topic_marker(tx, lookahead=20):
    """段首 lookahead 字符内是否出现口述话题转折标记(如"第四个点""第五个就是")。"""
    return bool(TOPIC_MARKER_RE.search((tx or "")[:lookahead]))


def segments_to_markdown(segments):
    """把 ASR segments 聚合成段落 Markdown。
    段落 = 语义小标题(规则法) + 段落级时间戳 + 逐字全文。
    分行策略:引擎中文通常自带标点,按"语音片段(segment)边界"分行;
    片段内部若有标点再进一步拆句 —— 两种情况下都能自动换行。"""
    if not segments:
        return "_(未识别到语音)_"
    paras = []  # (start, end, [seg_texts])
    cur_start, cur_end, cur_text = None, None, []
    for s in segments:
        st = float(s.get("start", 0))
        en = float(s.get("end", 0))
        tx = (s.get("text") or "").strip()
        if not tx:
            continue
        if cur_start is None:
            cur_start, cur_end, cur_text = st, en, [tx]
        elif _is_topic_marker(tx) and len(cur_text) >= 2:
            # 口述话题转折(如"第四个点""第五个就是")无条件切段,
            # 保证话题完整成段,不被 60s 聚类拦腰切断
            paras.append((cur_start, cur_end, cur_text))
            cur_start, cur_end, cur_text = st, en, [tx]
        elif (st - cur_start) >= 60 and len(cur_text) >= 2:
            paras.append((cur_start, cur_end, cur_text))
            cur_start, cur_end, cur_text = st, en, [tx]
        else:
            cur_end = en
            cur_text.append(tx)
    if cur_start is not None:
        paras.append((cur_start, cur_end, cur_text))

    parts = []
    for i, (st, en, texts) in enumerate(paras, 1):
        joined = "".join(texts)
        kws = extract_keywords(joined, 2)
        title = gen_section_title(joined, kws)
        header = f"## {i}. {title} [{_fmt_mmss(int(st))} - {_fmt_mmss(int(en))}]"
        lines = []
        for tx in texts:
            sents = split_sentences(tx)
            if sents:
                lines.extend(sents)
            else:
                # ASR 无标点时:按口语连词切子句 + 补句尾标点,保证可读
                for c in split_clauses(tx):
                    lines.append(add_sentence_punct(c))
        parts.append(f"{header}\n\n" + "\n".join(lines))
    return "\n\n".join(parts)


def estimate_local_time(duration):
    """FunASR SenseVoice-Small 耗时估算:M4 CPU 约 6x 实时 + 提音频/加载约 60s。"""
    if not duration or duration <= 0:
        return None, 1
    return int(duration / 8) + 40, 1


# ─── 分段处理 ─────────────────────────────────────────

def _fmt_mmss(sec):
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"




SEC_HEADER_RE = re.compile(
    r"^##\s*(\d+)[\.、\)\s]\s*(.*?)\s*\[\s*(\d+):(\d+)\s*[-–~]\s*(\d+):(\d+)\s*\]",
    re.MULTILINE,
)


def _parse_sections(md):
    """把一段 markdown 拆成 [(title, start_sec, end_sec, body), ...]。"""
    matches = list(SEC_HEADER_RE.finditer(md))
    sections = []
    for i, m in enumerate(matches):
        _, title, sm, ss, em, es = m.groups()
        start = int(sm) * 60 + int(ss)
        end = int(em) * 60 + int(es)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[body_start:body_end].strip()
        sections.append((title.strip(), start, end, body))
    return sections




# ─── 视频探测 + 耗时预估 ────────────────────────────────

def _ytdlp_probe(url):
    """yt-dlp --dump-json 拿元信息(youtube 等用)。"""
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
    }


def probe_video(input_path):
    """快速探测视频元信息。
    返回:
      {
        platform, title, duration, n_segs, est_sec,
        cached_info  -- 若已经从 platform_extract 拿到 URL,后续可复用
      }
    """
    if is_url(input_path):
        platform = detect_platform(input_path)
        if platform == "wechat_channels":
            return probe_via_video_download(input_path)
        elif platform in ("xiaohongshu", "douyin", "bilibili"):
            # 调 headless 提取器,顺便缓存视频/音频 URL,后续 download 不再重启浏览器
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from platform_extractor import extract as platform_extract
            info = platform_extract(input_path, headless=True)
            duration = info.get("duration") or 0
            return {
                "platform": platform,
                "title": info.get("title") or "",
                "duration": int(duration),
                "cached_info": info,
            }
        else:
            # YouTube / 其他平台
            return _ytdlp_probe(input_path)
    else:
        # 本地文件
        meta = get_video_info(input_path)
        return {
            "platform": "local",
            "title": Path(input_path).stem,
            "duration": int(meta["duration"]),
            "cached_info": None,
        }


def fmt_duration_human(sec):
    if not sec or sec <= 0: return "未知"
    sec = int(sec)
    if sec < 60: return f"{sec}秒"
    m, s = sec // 60, sec % 60
    if sec < 3600: return f"{m}分{s:02d}秒"
    h, m = m // 60, m % 60
    return f"{h}小时{m:02d}分"


def fmt_estimate_range(sec):
    """耗时给个 ±20% 范围,更诚实。"""
    if not sec: return "未知"
    lo = int(sec * 0.8)
    hi = int(sec * 1.3)
    return f"{fmt_duration_human(lo)} ~ {fmt_duration_human(hi)}"


def print_probe_report(meta, est_sec, n_segs):
    bar = "═" * 55
    sep = "─" * 55
    platform_zh = {
        "xiaohongshu": "小红书", "douyin": "抖音",
        "bilibili": "B 站", "youtube": "YouTube",
        "wechat_channels": "微信视频号",
        "local": "本地文件", "unknown": "未知平台",
    }.get(meta["platform"], meta["platform"])

    print(bar, file=sys.stderr)
    print("  📊 视频探测", file=sys.stderr)
    print(sep, file=sys.stderr)
    print(f"  平台:      {platform_zh}", file=sys.stderr)
    title = meta.get("title") or "(未抓到标题)"
    print(f"  标题:      {title}", file=sys.stderr)
    d = meta.get("duration") or 0
    print(f"  时长:      {fmt_duration_human(d)}", file=sys.stderr)
    seg_note = f"{n_segs} 段(每段 ≤ 6 分钟)" if n_segs > 1 else "1 段(短视频整体处理)"
    print(f"  分段:      {seg_note}", file=sys.stderr)
    if est_sec:
        print(f"  预估耗时:  {fmt_estimate_range(est_sec)}", file=sys.stderr)
    print(bar, file=sys.stderr)


# ─── 主流程 ────────────────────────────────────────────

def safe_filename(name, max_len=60):
    """把任意字符串清成安全的文件名"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name).strip()
    return name[:max_len] or "transcript"


def run(input_path, title=None, output_dir=None, save_md=True):
    if not check_ffmpeg():
        print("[ERROR] ffmpeg 未安装!请运行: brew install ffmpeg", file=sys.stderr)
        sys.exit(1)

    # ── Step 0: 探测 + 评估 ──
    print("[Step 0/3] 探测视频元信息...", file=sys.stderr)
    try:
        meta = probe_video(input_path)
    except Exception as e:
        print(f"[ERROR] 探测失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not meta.get("duration"):
        print("[WARN] 未拿到视频时长,无法预估耗时;仍将继续。", file=sys.stderr)

    est_sec, n_segs = estimate_local_time(meta.get("duration", 0))
    print_probe_report(meta, est_sec, n_segs)

    # 标题优先级:用户传入 > probe 拿到的
    if not title and meta.get("title"):
        title = meta["title"]

    cached_info = meta.get("cached_info")

    # ── Step 1: 下载 ──
    if is_url(input_path):
        if meta["platform"] == "wechat_channels":
            print(f"\n[Step 1/3] 调用 video-download 下载视频号到本地", file=sys.stderr)
            try:
                video_path, dl_title = download_via_video_download(input_path)
                if not title and dl_title:
                    title = dl_title
            except RuntimeError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                sys.exit(1)
        elif is_browser_only_platform(input_path):
            print(f"\n[Step 1/3] 后台浏览器抓取直链 + 下载", file=sys.stderr)
            video_path, _ = download_via_browser(input_path, cached_info=cached_info)
        else:
            if not check_ytdlp():
                print("[ERROR] yt-dlp 未安装!请运行: pip install --break-system-packages yt-dlp", file=sys.stderr)
                sys.exit(1)
            print(f"\n[Step 1/3] 下载视频", file=sys.stderr)
            try:
                video_path = download_video(input_path)
            except RuntimeError as e:
                print(f"[ERROR] {e}", file=sys.stderr)
                sys.exit(1)
    else:
        if not os.path.exists(input_path):
            print(f"[ERROR] 视频文件不存在: {input_path}", file=sys.stderr)
            sys.exit(1)
        video_path = os.path.abspath(input_path)
        print(f"\n[Step 1/3] 使用本地视频: {os.path.basename(video_path)}", file=sys.stderr)

    # 总时长决定走 短视频整体压缩 还是 长视频先切片再分段压缩
    src_info = get_video_info(video_path)
    total_duration = src_info["duration"]

    # ── FunASR 引擎: SenseVoice-Small,中文最优,CPU 高速 ──
    print(f"\n[Step 2/3] 提取音频(16k wav)...", file=sys.stderr)
    wav_path = os.path.join(WORK_DIR, "audio.wav")
    if os.path.abspath(video_path) == os.path.abspath(wav_path):
        print("[INFO] 输入已是 16k wav,跳过提取", file=sys.stderr)
    else:
        try:
            extract_audio_wav(video_path, wav_path)
        except RuntimeError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
    print(f"[INFO] 音频 {os.path.getsize(wav_path)/1024/1024:.1f}MB,开始 FunASR 转录(SenseVoice-Small)...", file=sys.stderr)
    print(f"[Step 3/3] FunASR 转录(CPU,中文最优,自带标点)...", file=sys.stderr)
    try:
        segments = transcribe_funasr(wav_path, hotword=FUNASR_HOTWORD)
    except Exception as e:
        print(f"[ERROR] FunASR 转录失败: {e}", file=sys.stderr)
        print(f"  提示: 首次使用需联网下载模型(约 234M)。", file=sys.stderr)
        sys.exit(1)
    transcript_md = segments_to_markdown(segments)

    # 顶部:标题 + 元信息 + 关键词 + 目录(段数 > 3 时)
    header = ""
    if title:
        platform_zh = {
            "xiaohongshu": "小红书", "douyin": "抖音",
            "bilibili": "B站", "youtube": "YouTube",
            "wechat_channels": "微信视频号", "local": "本地文件", "unknown": "未知平台",
        }.get(meta.get("platform", ""), meta.get("platform", ""))
        source = input_path if is_url(input_path) else os.path.basename(input_path)
        engine_label = "FunASR(SenseVoice-Small)"
        gen_date = time.strftime("%Y-%m-%d %H:%M")
        link_part = f" | 链接: {input_path}" if is_url(input_path) else ""
        header = f"# {title}\n\n> 来源: {platform_zh}{link_part} | 时长 {int(total_duration//60)}:{int(total_duration%60):02d} | 引擎: {engine_label} | 生成: {gen_date}\n"
        doc_kws = extract_keywords(transcript_md, 4)
        if doc_kws:
            header += f"> 关键词: {' · '.join(doc_kws)}\n"
        header += "\n"
    toc = build_toc(transcript_md)
    final_md = header + toc + transcript_md

    # 默认存盘到 skill 目录,同时 stdout 直出全文
    if save_md:
        out_dir = output_dir or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)
        name_seed = title or Path(video_path).stem
        date_prefix = time.strftime("%Y-%m-%d")
        out_file = os.path.join(out_dir, f"{date_prefix}_{safe_filename(name_seed, 30)}_transcript.md")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(final_md)
        print(f"\n[OK] 逐字稿已保存: {out_file}", file=sys.stderr)
        # 同步生成 HTML 预览版(带 复制全文 / 下载 .md 按钮),供 agent 在预览面板展示
        html_file = os.path.splitext(out_file)[0] + ".html"
        try:
            html_str = md_to_html(final_md, download_name=os.path.basename(out_file))
            if html_str:
                with open(html_file, "w", encoding="utf-8") as f:
                    f.write(html_str)
                print(f"[OK] 预览版已生成: {html_file}", file=sys.stderr)
            else:
                print("[WARN] 未生成 HTML 预览(缺 markdown 库,不影响 .md)", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] HTML 预览生成失败(不影响 .md): {e}", file=sys.stderr)

    print("=" * 55, file=sys.stderr)
    print("[OK] 转录完成,完整逐字稿见 stdout", file=sys.stderr)

    # stdout 直接输出全文
    print(final_md)


def doctor():
    """依赖 + 配置体检。返回 0=全部就绪,1=有问题。"""
    print("=" * 55)
    print("  🩺 video-transcript 体检")
    print("=" * 55)
    issues = []

    # ffmpeg
    if check_ffmpeg():
        print("  ✓ ffmpeg")
    else:
        print("  ✗ ffmpeg 未安装")
        issues.append("brew install ffmpeg")

    # ffprobe(随 ffmpeg 一起)
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
        print("  ✓ ffprobe")
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("  ✗ ffprobe 未安装(随 ffmpeg 一起装)")
        issues.append("brew install ffmpeg")

    # Python 版本
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 8):
        print(f"  ✓ Python {py}")
    else:
        print(f"  ✗ Python {py} 太旧(需 ≥ 3.8)")
        issues.append("升级 Python 到 3.8+")

    # yt-dlp(可选,YouTube 用;抖音/小红书/B站走 headless 不需要)
    if check_ytdlp():
        print("  ✓ yt-dlp")
    else:
        print("  ⚠ yt-dlp 未安装(YouTube 视频会用不了,其他平台不影响)")

    # playwright + chromium
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                # 不实际启动,只检查可执行文件存在
                exe = p.chromium.executable_path
                if exe and os.path.exists(exe):
                    print(f"  ✓ playwright + chromium")
                else:
                    print(f"  ✗ chromium 没装")
                    issues.append("python3 -m playwright install chromium")
            except Exception as e:
                print(f"  ✗ chromium 不可用: {e}")
                issues.append("python3 -m playwright install chromium")
    except ImportError:
        print("  ✗ playwright 未安装")
        issues.append("pip install --break-system-packages playwright")
        issues.append("python3 -m playwright install chromium")

    # funasr(SenseVoice-Small,唯一引擎)
    try:
        import funasr
        print(f"  ✓ funasr({funasr.__version__}, SenseVoice-Small 中文转录)")
    except ImportError:
        print("  ✗ funasr 未安装(唯一转录引擎)")
        issues.append("pip install funasr torchaudio")

    # video-download(微信视频号下载桥接)
    vd_script = find_video_download_script()
    if vd_script:
        print(f"  ✓ video-download: {vd_script}")
    else:
        print("  ⚠ video-download 未安装(仅影响微信视频号转录和仅下载分流)")

    # .env 配置
    if os.path.exists(ENV_FILE):
        print(f"  ✓ .env 文件: {ENV_FILE}")
    else:
        print(f"  ⚠ 没找到 .env 文件: {ENV_FILE}")

    print("=" * 55)
    if issues:
        print(f"  ❌ 发现 {len(issues)} 个问题, 解决方法:")
        for x in issues:
            print(f"     - {x}")
        print(f"\n  或运行一键安装: bash {SKILL_DIR}/install.sh")
        return 1
    print("  ✅ 全部就绪")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="视频逐字稿提取工具(FunASR SenseVoice-Small,中文最优,CPU 高速)"
    )
    parser.add_argument("input", nargs="?",
                        help="视频URL(B站/YouTube/抖音/小红书/微信视频号) 或 本地文件路径;--doctor 时不需要")
    parser.add_argument("--title", default=None, help="视频标题(用于文档头)")
    parser.add_argument("--no-save", dest="save_md", action="store_false",
                        help="不写 .md 文件(默认会保存到 skill 目录的 outputs/)")
    parser.add_argument("--output-dir", default=None,
                        help=f"输出目录,默认 {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--doctor", action="store_true",
                        help="体检:检查所有依赖和配置是否就绪")
    parser.set_defaults(save_md=True)

    args = parser.parse_args()

    if args.doctor:
        sys.exit(doctor())

    if not args.input:
        parser.error("缺少 input 参数(视频 URL 或本地文件路径)。--doctor 体检模式下可省略。")

    run(args.input, title=args.title, output_dir=args.output_dir, save_md=args.save_md)


if __name__ == "__main__":
    main()
