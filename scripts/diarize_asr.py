#!/usr/bin/env python3
"""FunASR 说话人分离转录：paraformer-zh + fsmn-vad + ct-punc + CAM++。

比 SenseVoice 慢(约音频时长的 15-20%)，但输出带 Speaker 标签的 segments，
适合播客/访谈/会议等多说话人音频。模型首次使用自动从 modelscope 下载。

CLI: python3 diarize_asr.py --input <wav/m4a/mp4> [--output-json <path>]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ASR_MODEL = "paraformer-zh"
VAD_MODEL = "fsmn-vad"
PUNC_MODEL = "ct-punc"
SPK_MODEL = "cam++"

# 转录耗时 / 音频时长，实测 CPU 4 线程约 0.25(1 小时单集约 15 分钟)
REALTIME_FACTOR = 0.25


def quiet_logs() -> None:
    """funasr 直接用 root logger 的 logging.info，每个 VAD 批次都会刷热词行。

    转录期间把 root 提到 WARNING，让进度提示不被淹没；WARNING/ERROR 仍会打出来。
    子库(jieba 等)的记录靠 handler 级别把关，只调 logger 级别拦不住。
    """
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    for handler in root.handlers:
        handler.setLevel(logging.WARNING)
    for name in ("funasr", "modelscope", "jieba"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip().splitlines()
        raise RuntimeError((detail[-1] if detail else "command failed")[:500])
    return result


def media_duration(path: Path) -> float:
    result = _run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(result.stdout.strip())


def to_wav_16k(media: Path, wav: Path) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(media),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(wav),
        ]
    )


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<\|[^>]+\|>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ms(value: Any, *, default: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0, int(round(number)))


def normalize_segments(result: dict[str, Any], duration_ms: int) -> list[dict[str, Any]]:
    """把 FunASR 各种输出形态统一为单调递增的句级 segments。"""
    segments: list[dict[str, Any]] = []
    sentence_info = result.get("sentence_info")
    if isinstance(sentence_info, list):
        for item in sentence_info:
            if not isinstance(item, dict):
                continue
            text = _clean_text(item.get("text") or item.get("sentence"))
            if not text:
                continue
            start = _ms(item.get("start"), default=0)
            end = _ms(item.get("end"), default=start + 1)
            segments.append(
                {
                    "start_ms": start,
                    "end_ms": max(start + 1, end),
                    "speaker": f"Speaker {item['spk']}" if item.get("spk") is not None else None,
                    "text": text,
                }
            )

    if not segments:
        text = _clean_text(result.get("text"))
        if text:
            segments.append(
                {"start_ms": 0, "end_ms": max(1, duration_ms), "speaker": None, "text": text}
            )

    normalized: list[dict[str, Any]] = []
    previous_end = 0
    for index, segment in enumerate(sorted(segments, key=lambda item: item["start_ms"]), start=1):
        start = min(max(previous_end, int(segment["start_ms"])), max(0, duration_ms - 1))
        end = min(max(start + 1, int(segment["end_ms"])), duration_ms)
        normalized.append({"id": index, **segment, "start_ms": start, "end_ms": end})
        previous_end = end
    return normalized


_MODEL: Any = None
_PUNC_ONLY: Any = None


def get_model(cpu_threads: int = 4, quiet: bool = True) -> Any:
    """进程内复用同一个 AutoModel(含 ASR/VAD/punc/说话人四个子模型)。"""
    global _MODEL
    if _MODEL is None:
        from funasr import AutoModel

        if quiet:
            quiet_logs()
        _MODEL = AutoModel(
            model=ASR_MODEL,
            vad_model=VAD_MODEL,
            punc_model=PUNC_MODEL,
            spk_model=SPK_MODEL,
            device="cpu",
            disable_update=True,
            disable_pbar=quiet,
            disable_log=quiet,
            ncpu=cpu_threads,
        )
        # funasr 建模过程中会重配 logging，建完再压一次才管得住推理期的日志
        if quiet:
            quiet_logs()
    return _MODEL


def _fmt_span(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}秒"
    if s < 3600:
        return f"{s // 60}分{s % 60:02d}秒"
    return f"{s // 3600}小时{(s % 3600) // 60:02d}分"


class _Heartbeat:
    """转录是一次不可中断的推理调用，用心跳告诉用户还在跑、大约还要多久。

    不切块是有意的：CAM++ 的说话人编号只在单次推理内一致，
    分块会让同一个人在不同块里拿到不同编号。
    """

    def __init__(self, est_total_sec: float, interval: float = 30.0, label: str = "转录中"):
        self.est_total_sec = max(0.0, est_total_sec)
        self.interval = interval
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_Heartbeat":
        start = time.time()

        def loop() -> None:
            while not self._stop.wait(self.interval):
                elapsed = time.time() - start
                msg = f"  [{self.label}] 已跑 {_fmt_span(elapsed)}"
                if self.est_total_sec > 0:
                    pct = min(99.0, elapsed / self.est_total_sec * 100)
                    remain = self.est_total_sec - elapsed
                    msg += f"，约 {pct:.0f}%"
                    msg += (
                        f"，预计还需 {_fmt_span(remain)}" if remain > 0 else "，即将完成"
                    )
                print(msg, file=sys.stderr, flush=True)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)


def punctuate(texts: list[str]) -> list[str] | None:
    """给无标点文本补标点。

    优先复用 get_model() 里已加载的 ct-punc 子模型(转录后调用即零额外加载)；
    独立调用后处理时才单独加载一份 ct-punc。
    """
    runner = None
    if _MODEL is not None and getattr(_MODEL, "punc_model", None) is not None:
        model = _MODEL

        def runner(text: str) -> str:  # noqa: F811 - 复用已加载子模型
            res = model.inference(text, model=model.punc_model, kwargs=model.punc_kwargs)
            return str(res[0]["text"]) if res and res[0].get("text") else text

    if runner is None:
        global _PUNC_ONLY
        try:
            if _PUNC_ONLY is None:
                from funasr import AutoModel

                quiet_logs()
                _PUNC_ONLY = AutoModel(
                    model=PUNC_MODEL, disable_update=True, disable_pbar=True, disable_log=True
                )
                quiet_logs()
        except Exception:  # noqa: BLE001 - 交由调用方回退规则标点
            return None
        standalone = _PUNC_ONLY

        def runner(text: str) -> str:  # noqa: F811
            res = standalone.generate(input=text)
            return str(res[0]["text"]) if res and res[0].get("text") else text

    out: list[str] = []
    for text in texts:
        try:
            out.append(runner(text))
        except Exception:  # noqa: BLE001 - 单条失败不影响整体
            out.append(text)
    return out


def diarize_wav(
    wav_path: str | Path,
    *,
    duration_sec: float | None = None,
    hotword: str | None = None,
    cpu_threads: int = 4,
    batch_size_s: int = 120,
    progress: bool = True,
) -> list[dict[str, Any]]:
    """转录 16k wav，返回带 speaker 的 segments 列表。"""
    wav = Path(wav_path)
    if duration_sec is None:
        duration_sec = media_duration(wav)

    model = get_model(cpu_threads)
    kwargs: dict[str, Any] = {}
    if hotword:
        kwargs["hotword"] = hotword

    def _run() -> Any:
        return model.generate(
            input=str(wav),
            batch_size_s=batch_size_s,
            sentence_timestamp=True,
            language="zh",
            use_itn=True,
            **kwargs,
        )

    if progress:
        with _Heartbeat(est_total_sec=duration_sec * REALTIME_FACTOR):
            generated = _run()
    else:
        generated = _run()
    if not generated or not isinstance(generated[0], dict):
        raise RuntimeError("FunASR 没有返回可用结果")
    return normalize_segments(generated[0], int(round(duration_sec * 1000)))


def diarize_media(
    media_path: str | Path,
    *,
    work_dir: str | Path = "/tmp/video-transcript",
    hotword: str | None = None,
    cpu_threads: int = 4,
) -> tuple[list[dict[str, Any]], float]:
    """任意媒体文件 → (segments, duration_sec)。非 wav 会先转 16k 单声道。"""
    media = Path(media_path)
    duration = media_duration(media)
    if media.suffix.lower() == ".wav":
        wav = media
        cleanup = False
    else:
        wav = Path(work_dir) / "diarize-audio.wav"
        to_wav_16k(media, wav)
        cleanup = True
    try:
        segments = diarize_wav(
            wav, duration_sec=duration, hotword=hotword, cpu_threads=cpu_threads
        )
    finally:
        if cleanup and wav.exists():
            wav.unlink(missing_ok=True)
    return segments, duration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--hotword", default=None)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("[ERROR] 需要 ffmpeg / ffprobe", file=sys.stderr)
        return 1

    segments, duration = diarize_media(
        args.input, hotword=args.hotword, cpu_threads=args.cpu_threads
    )
    payload = {
        "schema_version": 1,
        "backend": "funasr",
        "model": ASR_MODEL,
        "vad_model": VAD_MODEL,
        "punc_model": PUNC_MODEL,
        "speaker_model": SPK_MODEL,
        "media": str(args.input.resolve()),
        "duration_seconds": duration,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "segments": segments,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text + "\n", encoding="utf-8")
        print(f"[OK] {args.output_json} ({len(segments)} segments)", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
