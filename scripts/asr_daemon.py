#!/usr/bin/env python3
"""FunASR SenseVoice 常驻进程:模型只加载一次,后续转录走 Unix socket。

用法:
  python asr_daemon.py --start     # 后台启动(已在跑则复用)
  python asr_daemon.py --serve     # 前台服务
  python asr_daemon.py --stop
  python asr_daemon.py --status
  python asr_daemon.py --warmup    # 启动并等到模型就绪

协议:一行一条 JSON。
  {"cmd":"ping"}
  {"cmd":"transcribe","wav":"/path.wav","hotword":null,"offset":0}
  {"cmd":"shutdown"}
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time

SOCK_PATH = os.environ.get("VT_ASR_SOCK", "/tmp/video-transcript-asr.sock")
PID_PATH = os.environ.get("VT_ASR_PID", "/tmp/video-transcript-asr.pid")
LOG_PATH = os.environ.get("VT_ASR_LOG", "/tmp/video-transcript-asr.log")
IDLE_SEC = int(os.environ.get("VT_ASR_IDLE", "1800"))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid():
    if not os.path.exists(PID_PATH):
        return None
    try:
        pid = int(open(PID_PATH, encoding="utf-8").read().strip())
    except (ValueError, OSError):
        return None
    if _pid_alive(pid):
        return pid
    _unlink(PID_PATH)
    return None


def _recv_line(conn, timeout=600):
    conn.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
    if not buf:
        return None
    line, _, _rest = buf.partition(b"\n")
    return json.loads(line.decode("utf-8"))


def _send_line(conn, obj):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def client_request(payload, timeout=600):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(SOCK_PATH)
    try:
        _send_line(sock, payload)
        return _recv_line(sock, timeout=timeout)
    finally:
        sock.close()


def ping(timeout=2):
    try:
        return client_request({"cmd": "ping"}, timeout=timeout)
    except Exception:
        return None


def transcribe_via_daemon(wav_path, hotword=None, offset=0, timeout=900):
    resp = client_request(
        {"cmd": "transcribe", "wav": wav_path, "hotword": hotword, "offset": offset},
        timeout=timeout,
    )
    if not resp or not resp.get("ok"):
        raise RuntimeError((resp or {}).get("error") or "daemon 转录失败")
    return resp.get("segments") or []


def start_background(python_exe=None):
    existing = ping(timeout=1)
    if existing and existing.get("ok"):
        return True
    python_exe = python_exe or sys.executable
    script = os.path.abspath(__file__)
    with open(LOG_PATH, "ab") as logf:
        subprocess.Popen(
            [python_exe, script, "--serve"],
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return True


def wait_until_ready(timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = ping(timeout=2)
        if last and last.get("ok") and last.get("ready"):
            return last
        time.sleep(0.6)
    return last


def ensure_daemon(timeout=90, python_exe=None):
    """保证 daemon 在跑且模型已就绪。成功返回 ping 结果,失败返回 None。"""
    info = ping(timeout=1)
    if info and info.get("ok") and info.get("ready"):
        return info
    start_background(python_exe=python_exe)
    return wait_until_ready(timeout=timeout)


def stop_daemon():
    info = ping(timeout=1)
    if info and info.get("ok"):
        try:
            client_request({"cmd": "shutdown"}, timeout=3)
        except Exception:
            pass
    pid = read_pid()
    if pid:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    _unlink(SOCK_PATH)
    _unlink(PID_PATH)
    return True


def _shift_segments(segments, offset):
    if not offset:
        return segments
    out = []
    for seg in segments:
        item = dict(seg)
        item["start"] = round(float(item.get("start") or 0) + offset, 2)
        item["end"] = round(float(item.get("end") or 0) + offset, 2)
        out.append(item)
    return out


def _load_model():
    from funasr import AutoModel

    device = os.environ.get("FUNASR_DEVICE") or "cpu"
    log(f"[asr-daemon] 加载 SenseVoice-Small device={device}")
    t0 = time.time()
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="fsmn-vad",
        punc_model="ct-punc-c",
        device=device,
        disable_update=True,
    )
    log(f"[asr-daemon] 模型就绪 {time.time() - t0:.1f}s")
    return model


def _wav_duration(wav_path):
    try:
        import wave
        with wave.open(wav_path, "r") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def transcribe_with_model(model, wav_path, hotword=None):
    import re

    duration = _wav_duration(wav_path)
    gen_kwargs = {"input": wav_path, "batch_size_s": 300}
    if hotword:
        gen_kwargs["hotword"] = hotword
    res = model.generate(**gen_kwargs)
    if not res:
        return []
    raw_text = (res[0].get("text") or "").strip()
    if not raw_text:
        return []
    sentences = re.split(r"(?<=[。！？!?…])", raw_text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [{"start": 0.0, "end": duration, "text": raw_text}]
    total_chars = sum(len(s) for s in sentences) or 1
    segments = []
    char_offset = 0
    for sent in sentences:
        ratio = char_offset / total_chars
        next_ratio = (char_offset + len(sent)) / total_chars
        segments.append({
            "start": round(ratio * duration, 2),
            "end": round(next_ratio * duration, 2),
            "text": sent,
        })
        char_offset += len(sent)
    return segments


class _Worker:
    def __init__(self):
        self.ready = False
        self.error = None
        self.model = None
        self.last_used = time.time()
        self.lock = threading.Lock()

    def load(self):
        try:
            self.model = _load_model()
            self.ready = True
        except Exception as exc:
            self.error = str(exc)
            log(f"[asr-daemon] 加载失败: {exc}")

    def transcribe(self, wav_path, hotword=None, offset=0):
        with self.lock:
            self.last_used = time.time()
            segs = transcribe_with_model(self.model, wav_path, hotword=hotword)
            return _shift_segments(segs, offset)


def serve():
    worker = _Worker()
    loader = threading.Thread(target=worker.load, daemon=True)
    loader.start()

    _unlink(SOCK_PATH)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCK_PATH)
    server.listen(4)
    server.settimeout(1.0)
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    log(f"[asr-daemon] listening {SOCK_PATH} pid={os.getpid()}")

    running = True
    while running:
        if worker.ready and (time.time() - worker.last_used) > IDLE_SEC:
            log("[asr-daemon] idle timeout, exit")
            break
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            req = _recv_line(conn, timeout=30)
            if not req:
                continue
            cmd = req.get("cmd")
            if cmd == "ping":
                _send_line(conn, {
                    "ok": True,
                    "ready": worker.ready,
                    "error": worker.error,
                    "pid": os.getpid(),
                })
            elif cmd == "shutdown":
                _send_line(conn, {"ok": True})
                running = False
            elif cmd == "transcribe":
                if worker.error:
                    _send_line(conn, {"ok": False, "error": worker.error})
                elif not worker.ready:
                    loader.join(timeout=120)
                    if not worker.ready:
                        _send_line(conn, {"ok": False, "error": worker.error or "模型仍在加载"})
                        continue
                try:
                    segs = worker.transcribe(
                        req.get("wav"),
                        hotword=req.get("hotword"),
                        offset=float(req.get("offset") or 0),
                    )
                    _send_line(conn, {"ok": True, "segments": segs})
                except Exception as exc:
                    _send_line(conn, {"ok": False, "error": str(exc)})
            else:
                _send_line(conn, {"ok": False, "error": f"unknown cmd {cmd}"})
        except Exception as exc:
            try:
                _send_line(conn, {"ok": False, "error": str(exc)})
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    server.close()
    _unlink(SOCK_PATH)
    _unlink(PID_PATH)
    log("[asr-daemon] stopped")


def main():
    ap = argparse.ArgumentParser(description="FunASR 常驻进程")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--warmup", action="store_true")
    args = ap.parse_args()

    if args.serve:
        serve()
        return 0
    if args.stop:
        stop_daemon()
        print("stopped")
        return 0
    if args.status:
        info = ping(timeout=2)
        print(json.dumps(info or {"ok": False, "error": "not running"}, ensure_ascii=False))
        return 0 if info and info.get("ok") else 1
    if args.start or args.warmup:
        start_background()
        info = wait_until_ready(90 if args.warmup else 8)
        print(json.dumps(info or {"ok": True, "ready": False, "starting": True}, ensure_ascii=False))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
