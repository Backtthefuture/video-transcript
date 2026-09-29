"""Durable single-asset CLI adapter for upstream skills. Standard library only."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import tempfile
import time

TEXT_KEYS = ('transcript_path', 'preorganized_path', 'polish_brief_path')
ATTENTION = {'response_needs_review', 'mapping_needs_review', 'upload_outcome_unknown',
             'auth_required', 'qa_failed', 'download_failed', 'failed', 'outputs_missing',
             'source_mismatch', 'busy'}


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def atomic(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextlib.contextmanager
def lock(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('TASK_BUSY: 已有调用方处理此任务')
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


def nonempty(path):
    return bool(path) and Path(path).is_file() and Path(path).stat().st_size > 0


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def parse_outputs(stdout, stderr=''):
    """Current stdout JSON, plus the old marker protocol for saved artifacts."""
    for text in (stdout, stderr, stderr + '\n' + stdout):
        candidates = [text.strip()]
        if '----- VT_OUTPUTS -----' in text:
            candidates.insert(0, text.rsplit('----- VT_OUTPUTS -----', 1)[1].strip())
        for candidate in candidates:
            try:
                value = json.loads(candidate)
                if isinstance(value, dict): return value
            except (ValueError, TypeError): pass
    return {}


def session(directory):
    path = Path(directory)/'session.json'
    with lock(path.with_suffix('.lock')):
        data = read(path)
        if not data:
            data = {'session_id': str(int(time.time())) + '-' + secrets.token_hex(3)}
            atomic(path, data)
    return data['session_id']


def execute(command, directory, timeout, progress=None):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    stdout, stderr = directory/'stdout.json', directory/'stderr.log'
    started = time.monotonic()
    with stdout.open('w') as out, stderr.open('w') as err:
        proc = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
        last = -30
        try:
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL); proc.wait()
                    return 124, {}
                if progress and elapsed-last >= 30:
                    progress({'elapsed_seconds': round(elapsed), 'phase': directory.name})
                    last = elapsed
                time.sleep(.2)
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait()
            raise
    return proc.returncode, parse_outputs(stdout.read_text(), stderr.read_text())


def snapshot(pointer):
    manifest = read(pointer['batch_manifest'])
    if manifest.get('asset_ids') != [pointer['asset_id']]:
        raise ValueError('MAPPING_CHANGED: 批次成员与已保存映射不一致')
    state_path = Path(manifest['output_dir'])/'.quark-v1/items'/pointer['asset_id']/'state.json'
    s = read(state_path)
    if s.get('asset_id') != pointer['asset_id']:
        raise ValueError('MAPPING_CHANGED: 条目身份不一致')
    outputs = s.get('outputs', {})
    status = s['status']
    if status == 'text_received' and not all(nonempty(outputs.get(k)) for k in TEXT_KEYS):
        status = 'outputs_missing'
    # Only recorded, hash-verified copies can feed screenshots, never directory globs.
    media=None; mismatched=False
    for candidate in dict.fromkeys(s.get(k) for k in ('local_path','upload_path','local_source')):
        if nonempty(candidate):
            if s.get('sha256') and digest(candidate)==s['sha256']:
                media=candidate;break
            mismatched=True
    return {'schema_version': 1, 'asset_id': s['asset_id'],
            'batch_manifest': pointer['batch_manifest'], 'state_path': str(state_path),
            'backend_status': s['status'], 'transcript_status': status, 'outputs': outputs,
            'video_path': media if s.get('has_video') and nonempty(media) else None,
            'media_path': media if nonempty(media) else None, 'media_sha256': s.get('sha256'),
            'media_integrity':'verified' if media else 'mismatch' if mismatched else 'missing',
            'duration': s.get('duration'), 'source_url': s.get('source_url'),
            'next_poll_at': s.get('next_poll_at'), 'error': s.get('error'),
            'candidate_raw_path': s.get('candidate_raw_path'),
            'quality': s.get('candidate_quality', s.get('quality', {})),
            'full_verbatim_verified': False}


def repair_media(pointer, home, python, timeout):
    """Explicit download-only recovery; identical bytes required, no upload or QA."""
    current=snapshot(pointer)
    if current.get('video_path'):return current
    state_path=Path(current['state_path']);s=read(state_path)
    url=s.get('source_url') or s.get('input','')
    if not s.get('sha256') or not str(url).startswith(('http://','https://')):
        raise ValueError('MEDIA_REPAIR_NEEDS_SOURCE: 缺可核对的原链接或哈希')
    downloader=Path(home).parent/'video-download/scripts/download_video.py'
    if not downloader.is_file():raise ValueError('MEDIA_REPAIR_NEEDS_DOWNLOADER: 指定后端同安装根缺 video-download')
    with lock(state_path.with_name('task.lock')):
        s=read(state_path)
        directory=state_path.parent/'media-repairs'/secrets.token_hex(8)
        command=[str(python),str(downloader),url,'--output-dir',str(directory),'--json']
        if 'weixin.qq.com' in url:command+=['--wechat-resolver','yuanbao-login']
        proc=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
        try:result=json.loads(proc.stdout)
        except ValueError:raise RuntimeError('MEDIA_REPAIR_FAILED: 下载器未返回有效 JSON')
        path=Path(result['path']).resolve() if result.get('path') else None
        if proc.returncode or result.get('ok') is not True or not path or not nonempty(path) or directory.resolve() not in path.parents:
            raise RuntimeError('MEDIA_REPAIR_FAILED: 下载结果无可靠绑定')
        if digest(path)!=s['sha256']:
            s['media_repair_error']='下载字节与原资产不同，保留候选等待核对'
            s['media_repair_candidate']=str(path);atomic(state_path,s)
            raise ValueError('MEDIA_REPAIR_MISMATCH: 未替换原资产、未上传或重新取稿')
        s['local_path']=str(path)
        if s.get('outputs'):s['outputs']['video_path']=str(path)
        s.pop('media_repair_error',None);atomic(state_path,s)
    return snapshot(pointer)


def run_job(directory, vt_home, vt_python, spec, request_file=None, timeout=1800,
            retry_errors=False, status_only=False, session_id=None, progress=None, repair_missing_media=False):
    """One non-waiting tick. Register locally and persist the pointer before I/O."""
    directory = Path(directory).resolve(); directory.mkdir(parents=True, exist_ok=True)
    home = Path(vt_home).expanduser().resolve()
    with lock(directory/'caller.lock'):
        pointer_path = directory/'job.json'; pointer = read(pointer_path)
        identity = {k: spec.get(k) for k in ('input', 'source_url')}
        if pointer and pointer.get('identity') != identity:
            raise ValueError('SOURCE_MISMATCH: 该任务目录已绑定其他来源，使用新目录')
        if not status_only and (not request_file or not nonempty(request_file)):
            raise ValueError('需要 --session-input-file 保存本轮原始用户请求')
        base = [str(vt_python), str(home/'scripts/transcript.py')]
        if not pointer:
            atomic(directory/'input.json', [spec])
            command = base + ['--batch', str(directory/'input.json'), '--output-dir',
                              str(directory/'store'), '--status']
            if session_id: command += ['--session-id', session_id]
            code, summary = execute(command, directory/'register', timeout, progress)
            items = summary.get('items', [])
            if not summary.get('batch_manifest') or len(items) != 1 or not items[0].get('asset_id'):
                raise RuntimeError('REGISTER_FAILED: 未得到单条云端映射；未调用上传')
            pointer = {'schema_version': 1, 'identity': identity,
                       'batch_manifest': summary['batch_manifest'], 'asset_id': items[0]['asset_id'],
                       'vt_home': str(home), 'created_at': time.time()}
            # Crash after registration is harmless; deterministic registration can be repeated.
            atomic(pointer_path, pointer)
        elif pointer.get('vt_home') != str(home):
            raise ValueError('VT_HOME_CHANGED: 恢复时必须使用原后端目录')
        result = snapshot(pointer)
        if status_only: return result
        if repair_missing_media and not result.get('video_path'):
            result=repair_media(pointer,home,vt_python,timeout)
        status = result['transcript_status']
        # Rebuild missing derived text without a cloud request when raw is intact.
        reformat = (status == 'outputs_missing' and nonempty(result['outputs'].get('transcript_path')))
        due = not (status == 'waiting_analysis' and time.time() < (result.get('next_poll_at') or 0))
        if status in ('text_received','no_audio') or not due or (status in ATTENTION and not retry_errors and not reformat):
            return result
        command = base + ['--resume', pointer['batch_manifest'], '--session-input-file',
                          str(Path(request_file).resolve()), '--wait', '0']
        if reformat: command += ['--reformat']
        elif retry_errors: command += ['--retry-errors']
        attempt = directory/'calls'/(str(time.time_ns())+'-'+secrets.token_hex(3))
        code, summary = execute(command, attempt, timeout, progress)
        result = snapshot(pointer)
        result['exit_code'] = code
        if code not in (0, 1, 2):
            result['invocation_error'] = 'deadline_exceeded' if code == 124 else f'exit_{code}'
        atomic(directory/'result.json', result)
        return result
