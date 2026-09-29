#!/usr/bin/env python3
"""Durable, cloud-only transcription via the installed official Quark CLI."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
ENGINE = '夸克网盘（文件问答取稿）'
QUERY = ('请完整输出你实际读取到的这个视频或音频对应的全部文字内容，按原有顺序从第一句到最后一句原样输出。'
         '保留第一人称、叙述、采访、解说和所有数字；不要评价文稿类别，不要求补出说话人，不要摘要、分析或改写，'
         '也不要补充文件以外的信息。如果系统只向你提供了部分片段，请在正文后明确说明可见范围，不要声称已覆盖完整内容。')


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value)
            out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def locked(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('TASK_BUSY: 同一文件已有任务运行，请稍后恢复')
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


def canonical(value):
    if value.startswith(('http://', 'https://')):
        p = urlparse(value); q = parse_qs(p.query)
        if p.hostname in ('www.douyin.com', 'douyin.com'):
            vid = (q.get('modal_id') or [''])[0]
            if not vid:
                match = re.search(r'/video/(\d+)', p.path); vid = match.group(1) if match else ''
            if vid.isdigit(): return 'https://www.douyin.com/video/' + vid
        return value.split('#')[0].rstrip('/')
    return str(Path(value).expanduser().resolve())


def locate(name, marker, override=None):
    if override:
        p = Path(override).expanduser().resolve()
        if not (p / marker).is_file(): raise RuntimeError('INVALID_SKILL_HOME: ' + str(p))
        return p
    for parent in (Path.home()/'.agents/skills', Path.home()/'.codex/skills', Path.home()/'.workbuddy/skills', ROOT.parent):
        p = parent/name
        if (p/marker).is_file(): return p.resolve()
    raise RuntimeError('MISSING_SKILL: ' + name)


def media_info(path):
    p = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                       capture_output=True, text=True, timeout=60)
    if p.returncode: raise RuntimeError('MEDIA_INVALID: ffprobe 无法读取文件')
    info = json.loads(p.stdout); streams = info.get('streams', [])
    duration = float(info.get('format', {}).get('duration') or 0)
    if duration <= 0: raise RuntimeError('MEDIA_INVALID: 未取得有效时长')
    return {'duration': duration, 'size': Path(path).stat().st_size,
            'has_audio': any(s.get('codec_type') == 'audio' for s in streams),
            'has_video': any(s.get('codec_type') == 'video' for s in streams)}


class Quark:
    def __init__(self, home, session_id, session_input):
        self.home = Path(home); self.session_id = session_id; self.session_input = session_input

    def call(self, action, args, directory):
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
        install = directory/'environment.log'
        with install.open('w') as out:
            p = subprocess.run(['bash', str(self.home/'scripts/install.sh')], stdout=out, stderr=subprocess.STDOUT, timeout=180)
        install.chmod(0o600)
        if p.returncode: raise RuntimeError('QUARK_ENV_FAILED: 查看 ' + str(install))
        command = ['node', str(self.home/'scripts/quark-drive.cjs'), action, *args,
                   '--session-id', self.session_id]
        if self.session_input: command += ['--session-input', self.session_input]
        log = directory/'response.jsonl'; err = directory/'stderr.log'
        timed_out = False
        with log.open('w') as out, err.open('w') as stderr:
            os.chmod(log, 0o600); os.chmod(err, 0o600)
            try: subprocess.run(command, stdout=out, stderr=stderr, timeout=900 if action=='upload' else 180)
            except subprocess.TimeoutExpired: timed_out = True
        rows = []
        for line in log.read_text().splitlines():
            try: rows.append(json.loads(line))
            except ValueError: pass
        result = next((r for r in reversed(rows) if r.get('type')=='result'), None)
        if result is None: result = {'code': 'OUTCOME_UNKNOWN', 'msg': '调用超时或未收到完整结果', 'data': {}}
        return {'rows': rows, 'result': result, 'timed_out': timed_out, 'log': str(log)}


def fid_identity(value):
    tail = value.rsplit('|', 1)[-1]
    return tail if '|' in value and tail else value


def artifact_rows(response):
    if response['result'].get('code') != 0: raise RuntimeError(response['result'].get('msg', 'SEARCH_FAILED'))
    artifacts = [r for r in response['rows'] if r.get('type') == 'artifact']
    if not artifacts: raise RuntimeError('SEARCH_ARTIFACT_MISSING: 不使用预览结果继续')
    path = Path(artifacts[-1]['data']['file_path'])
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def classify(result):
    code = result.get('code'); msg = result.get('msg', '')
    if code == -1505: return 'waiting_analysis'
    if code != 0 and re.search('未授权|认证|token|授权.*过期', msg, re.I): return 'auth_required'
    return 'qa_failed'


def response_check(raw, expected_name, duration):
    # These detect suspicious answers; they never establish verbatim completeness.
    marker = '**以上内容参考'
    body = raw.split(marker, 1)[0].strip()
    scope_note = ''
    if '**可见范围说明' in body:
        body, scope_note = body.split('**可见范围说明', 1)
        body = body.strip()
    flags = []
    if scope_note or re.search('无法确认是否|未包含后续|仅提供.{0,8}片段', raw): flags.append('scope_limit_declared')
    if not body: flags.append('empty_body')
    if expected_name and expected_name not in raw: flags.append('source_reference_missing')
    if len(body) < max(40, min(300, duration * .35)): flags.append('unusually_short')
    if re.search(r'无法.{0,12}(提供|读取|获取)|仅.{0,5}(部分片段|部分内容)|以下.{0,8}(摘要|总结)', body[:500]):
        flags.append('partial_or_non_transcript_answer')
    return body, {'flags': flags, 'body_characters': len(body), 'full_verbatim_verified': False,
                  'scope_note':scope_note, 'source_reference_present': bool(expected_name and expected_name in raw)}


def write_preorganized(s, directory):
    raw = Path(s['raw_path']).read_text(); body = s['body']
    # No sentence deletion or synthetic timestamps. Chaptering is text-length based.
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n|\n', body) if p.strip()]
    if len(paragraphs) <= 2 and len(body) > 700:
        paragraphs=[]; buf=''
        for part in re.split(r'(?<=[。！？])', body):
            buf += part
            if len(buf) >= 220: paragraphs.append(buf); buf=''
        if buf: paragraphs.append(buf)
    sections=[]; current=[]; count=0
    for para in paragraphs:
        current.append(para); count+=len(para)
        if count >= 650: sections.append(current); current=[]; count=0
    if current: sections.append(current)
    title = s.get('title') or s['asset_id']; duration=s.get('duration',0)
    text=[f'# {title}', '', f'> 来源: {s.get("platform", "音视频")} | 链接: {s.get("source_url", "")} | 时长 {int(duration)//60:02}:{int(duration)%60:02} | 转录: {ENGINE} | 生成: {time.strftime("%Y-%m-%d %H:%M")}',
          '> 状态: 已取回正文，完整性待核对；无句级时间轴，不自动识别人名。', '']
    brief={'mode':'patch_only', 'asset_id':s['asset_id'], 'raw_path':s['raw_path'], 'quality':s['quality'],
           'instruction':'保留全文与顺序，只做有依据的纠错、分段和标题润色；低确信词保留待核，不补写缺失内容。',
           'sections':[], 'patch_schema':{'title':'optional','headings':[], 'fixes':[{'from':'原词','to':'修正词','confidence':'high|low','basis':'依据'}], 'paragraph_edits':[]}}
    for i, paras in enumerate(sections,1):
        heading=f'正文 {i}'; text += [f'## {i}. {heading}', '', '\n\n'.join(paras), '']
        brief['sections'].append({'index':i,'heading':heading,'paragraph_count':len(paras),'excerpt':paras[0][:100]})
    text += ['---','','## 附：识别修正对照表（整理时改动）','','待模型校对；原始返回单独保存。']
    pre=Path(directory)/'预整理.md'; brief_path=Path(directory)/'polish_brief.json'
    atomic(pre, '\n'.join(text)+'\n'); atomic(brief_path, brief)
    s['outputs']={'transcript_path':s['raw_path'],'preorganized_path':str(pre),'polish_brief_path':str(brief_path),
                  'video_path':s.get('local_path') if s.get('has_video') else None, 'title':title,
                  'source_url':s.get('source_url'),'duration':duration,'engine':ENGINE,'asset_id':s['asset_id'],
                  'quality':s['quality']}


class Pipeline:
    def __init__(self, output, client, download_home=None):
        self.output=Path(output).resolve(); self.store=self.output/'.quark-v1'; self.client=client
        self.download_home=download_home

    def register(self, spec):
        spec={'input':spec} if isinstance(spec,str) else dict(spec)
        value=spec.get('input')
        if not value: raise ValueError('每条输入必须有 input；网盘文件用 quark:<完整FID>')
        if value.startswith('quark:'):
            identity='quark:'+fid_identity(value[6:]); kind='cloud'; meta={}
            if not spec.get('filename') or spec.get('size') is None:
                raise ValueError('网盘输入须提供 filename 和 size；先从官方 search/browse 完整 Artifact 选定文件')
        else:
            identity=canonical(value); kind='url' if identity.startswith(('http://','https://')) else 'local'; meta={}
            if kind=='local':
                path=Path(identity)
                if not path.is_file(): raise ValueError('本地文件不存在: '+identity)
                sha=digest(path); identity='sha256:'+sha; meta={'local_source':str(path),'sha256':sha}
        key=hashlib.sha256(identity.encode()).hexdigest()[:24]; directory=self.store/'items'/key
        directory.mkdir(parents=True,exist_ok=True)
        with locked(directory/'task.lock'):
            state=directory/'state.json'
            if state.exists():
                s=read(state)
                if value not in s['sources']: s['sources'].append(value); atomic(state,s)
            else:
                s={'schema_version':1,'asset_id':key,'identity':identity,'kind':kind,'sources':[value],
                   'source_url':value if kind=='url' else spec.get('source_url',''), 'input':value,
                   'title':spec.get('title',''),'status':'registered','attempts':[],'created_at':time.time(),**meta}
                if kind=='cloud': s.update(expected_name=spec['filename'],size=int(spec['size']),
                    duration=float(spec.get('duration',0)),qa_fid=value[6:],parent_fid=spec.get('parent_fid'),
                    platform='夸克网盘', title=spec.get('title') or spec['filename'])
                atomic(state,s)
        return key

    def attempt(self,s,d,action,args):
        a={'action':action,'id':secrets.token_hex(8),'started_at':time.time()}
        folder=d/'attempts'/a['id'];a['directory']=str(folder);s['attempts'].append(a);atomic(d/'state.json',s)
        result=self.client.call(action,args,folder)
        a.update(ended_at=time.time(),code=result['result'].get('code'),msg=result['result'].get('msg'),log=result['log'])
        atomic(d/'state.json',s);return result

    def acquire(self,s,d):
        if s['kind']=='local':
            src=Path(s['local_source']);suffix=src.suffix.lower() or '.media';dest=d/('media'+suffix)
            shutil.copy2(src,dest)
            if digest(dest)!=s['sha256']: raise RuntimeError('SOURCE_CHANGED: 登记后本地文件发生变化，请重新登记')
            s['title']=s['title'] or src.stem
        else:
            url=canonical(s['input']);host=urlparse(url).hostname or ''
            if host.endswith(('xiaoyuzhoufm.com','ximalaya.com','podcasts.apple.com')):
                download=d/'download';download.mkdir(exist_ok=True)
                if host.endswith('xiaoyuzhoufm.com'):
                    from podcast_extractor import extract_episode
                    import urllib.request
                    episode=extract_episode(url);dest=download/'episode.m4a'
                    with urllib.request.urlopen(episode['audio_url'], timeout=120) as incoming, dest.open('wb') as outgoing:
                        shutil.copyfileobj(incoming,outgoing)
                    s['title']=s['title'] or episode['title']
                else:
                    proc=subprocess.run(['yt-dlp','--no-playlist','-f','bestaudio','-o',str(download/'episode.%(ext)s'),'--print','after_move:filepath',url],capture_output=True,text=True,timeout=1200)
                    candidates=[Path(line) for line in proc.stdout.splitlines() if Path(line).is_file()]
                    if proc.returncode or len(candidates)!=1: raise RuntimeError('PODCAST_DOWNLOAD_FAILED: 请提供单集或本地音频')
                    dest=candidates[0];s['title']=s['title'] or '播客单集'
                s['platform']='播客';s['sha256']=digest(dest)
            else:
                home=locate('video-download','scripts/download_video.py',self.download_home)
                cmd=[sys.executable,str(home/'scripts/download_video.py'),canonical(s['input']),'--output-dir',str(d/'download'),'--json']
                if 'weixin.qq.com' in s['input']:
                    cmd+=['--wechat-resolver',os.environ.get('VIDEO_DOWNLOAD_WECHAT_RESOLVER') or 'yuanbao-login']
                r=subprocess.run(cmd,capture_output=True,text=True,timeout=1200)
                # Download JSON may contain signed media URLs; persist only selected metadata.
                try: data=json.loads(r.stdout)
                except ValueError: raise RuntimeError('DOWNLOAD_FAILED: 下载器未返回有效 JSON')
                if r.returncode or data.get('ok') is not True or not data.get('path'):
                    raise RuntimeError('DOWNLOAD_FAILED: '+str(data.get('error','下载器未确认成功'))[:300])
                dest=Path(data['path']).resolve()
                if not dest.is_file() or (d/'download').resolve() not in dest.parents:
                    raise RuntimeError('DOWNLOAD_PATH_UNBOUND: 文件不在本条独立下载目录')
                s['title']=s['title'] or data.get('title') or dest.stem
                s['platform']=data.get('platform','音视频');s['sha256']=digest(dest)
        s.update(local_path=str(dest),**media_info(dest))
        name='vt-'+s['asset_id']+dest.suffix.lower();upload=d/name
        if not upload.exists(): shutil.copy2(dest,upload)
        if digest(upload)!=s['sha256']: raise RuntimeError('LOCAL_COPY_MISMATCH')
        s.update(upload_path=str(upload),expected_name=name,status='downloaded');atomic(d/'state.json',s)

    def reconcile(self,s,d,cloud=False):
        # Reuse a complete artifact only after it has been bound and saved in state.
        args=['--keyword',s['expected_name'][:50],'--stdout-only']
        if s.get('parent_fid'): args+=['--parent-fid',s['parent_fid']]
        result=self.attempt(s,d,'search',args)
        if result['result'].get('code')!=0:
            s['status']=classify(result['result']);s['error']=result['result'].get('msg');return False
        rows=artifact_rows(result);atomic(d/'search-records.json',rows)
        matches=[r for r in rows if r.get('filename')==s['expected_name'] and r.get('size')==s['size']]
        if cloud: matches=[r for r in matches if fid_identity(r.get('fid',''))==fid_identity(s['qa_fid'])]
        else:
            def recent(r):
                try:return abs(float(r['created_at'])/1000-s['upload_started_at'])<=1800
                except (KeyError,ValueError,TypeError):return False
            matches=[r for r in matches if recent(r)]
        if s.get('duration'):
            matches=[r for r in matches if r.get('duration') is None or abs(float(r['duration'])-s['duration'])<2]
        if len(matches)!=1:
            s.update(status='mapping_needs_review',candidate_count=len(matches),error='网盘候选缺失或不唯一；不重传、不猜测');return False
        found=matches[0]
        s.pop('error',None)
        s.update(qa_fid=found['fid'],cloud_record=found,status='upload_verified',duration=s.get('duration') or found.get('duration',0),
                 mapping_basis='完整搜索结果：唯一文件名+大小；本机上传再核对创建时间；时长返回时核对；云端输入按官方FID尾串识别并使用最新完整FID', duration_readback_verified=found.get('duration') is not None and bool(s.get('duration')))
        return True

    def tick(self,key,refresh=False,reformat=False,retry_errors=False,accept_note=None):
        d=self.store/'items'/key
        with locked(d/'task.lock'):
            s=read(d/'state.json')
            try:
                if accept_note:
                    if s['status']!='response_needs_review' or not s.get('candidate_raw_path'):
                        raise RuntimeError('没有待接纳的候选正文')
                    raw=Path(s['candidate_raw_path']).read_text();body,quality=response_check(raw,s['expected_name'],s.get('duration',0))
                    s.update(raw_path=s['candidate_raw_path'],body=body,quality=quality,review_note=accept_note,status='text_received')
                    write_preorganized(s,d);return self.save(s,d)
                if reformat:
                    if not s.get('raw_path'): raise RuntimeError('NO_RAW: 尚未取回可用原文')
                    raw=Path(s['raw_path']).read_text();body,quality=response_check(raw,s['expected_name'],s.get('duration',0))
                    s.update(body=body,quality=quality)
                    if quality['flags'] and not s.get('review_note'):
                        s.update(status='response_needs_review',candidate_raw_path=s['raw_path'],candidate_quality=quality);return self.save(s,d)
                    write_preorganized(s,d);s['status']='text_received';return self.save(s,d)
                if s.get('raw_path') and s['status']=='text_received' and not refresh: return s
                if s['status'] in ('auth_required','qa_failed','download_failed','failed','response_needs_review') and not (retry_errors or refresh):return s
                if s['kind']!='cloud' and not s.get('upload_path'): self.acquire(s,d)
                if s.get('has_audio') is False:
                    s.update(status='no_audio',error='文件没有音轨；媒体保留，可单独检查画面')
                    return self.save(s,d)
                if s['kind']=='cloud' and not s.get('cloud_record'):
                    if not self.reconcile(s,d,cloud=True):return self.save(s,d)
                if s['kind']!='cloud' and not s.get('cloud_record'):
                    had_upload=any(a['action']=='upload' for a in s['attempts'])
                    if retry_errors and s.get('upload_rejected_auth'): had_upload=False
                    if not had_upload:
                        if digest(s['upload_path'])!=s['sha256']:raise RuntimeError('SOURCE_CHANGED: 上传副本校验失败')
                        s.update(status='upload_outcome_unknown',upload_started_at=time.time());self.save(s,d)
                        response=self.attempt(s,d,'upload',[s['upload_path']])
                        receipts=[r['data'] for r in response['rows'] if r.get('type')=='list' and r.get('code')==0 and
                                  r.get('data',{}).get('fileName')==s['expected_name'] and r['data'].get('fileSize')==s['size']]
                        if len(receipts)==1:
                            s.update(upload_fid=receipts[0]['fileId'],upload_record_id=receipts[0].get('recordId'),uploaded_at=time.time(),upload_rejected_auth=False);self.save(s,d)
                        elif classify(response['result'])=='auth_required':
                            s.update(status='auth_required',upload_rejected_auth=True,error=response['result'].get('msg'));return self.save(s,d)
                    # Including unknown outcomes: only search, never reissue upload.
                    if not self.reconcile(s,d):return self.save(s,d)
                    self.save(s,d)
                if not refresh and time.time()<s.get('next_poll_at',0):return s
                response=self.attempt(s,d,'qa',['--fid-list',s['qa_fid'],'--query',QUERY])
                result=response['result'];s['last_qa_message']=result.get('msg');s['last_qa_code']=result.get('code')
                if result.get('code')!=0:
                    s['status']=classify(result)
                    if s['status']=='waiting_analysis':
                        n=sum(a['action']=='qa' for a in s['attempts']);delay=60 if n<3 else 120 if n<8 else 300
                        s['next_poll_at']=time.time()+delay
                    return self.save(s,d)
                raw=result.get('data',{}).get('text_block',{}).get('text','')
                rawpath=Path(response['log']).with_name('raw.md');atomic(rawpath,raw)
                s['qa_task_id']=result.get('data',{}).get('task_id');s['candidate_raw_path']=str(rawpath)
                body,quality=response_check(raw,s['expected_name'],s.get('duration',0));s['candidate_quality']=quality
                if quality['flags']:
                    s['status']='response_needs_review';return self.save(s,d)
                s.pop('next_poll_at',None);s.pop('error',None)
                s.update(raw_path=str(rawpath),body=body,quality=quality,status='text_received',text_received_at=time.time())
                write_preorganized(s,d)
            except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as e:
                s['error']=str(e);s['status']='upload_outcome_unknown' if s.get('status')=='upload_outcome_unknown' else 'failed'
            return self.save(s,d)

    def save(self,s,d):
        atomic(d/'state.json',s);return s


def doctor(args):
    result={'engine':ENGINE,'local_asr_used':False,'checks':{}}
    for name in ('python3','node','ffprobe','ffmpeg'):result['checks'][name]=bool(shutil.which(name))
    for name,marker,override in [('quarkclouddrive','scripts/quark-drive.cjs',args.quark_home),('video-download','scripts/download_video.py',args.download_home)]:
        try:result['checks'][name]=str(locate(name,marker,override))
        except RuntimeError:result['checks'][name]=False
    result['authorization']='未探活；首次云端命令验证，失效时按夸克 Skill 登录'
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if all(result['checks'].values()) else 1


def main(argv=None):
    p=argparse.ArgumentParser(description='音视频 → 夸克云端取稿 → 预整理（无本地 ASR）')
    p.add_argument('input',nargs='?');p.add_argument('--batch',help='JSON数组：字符串或含input的对象')
    p.add_argument('--resume',help='批次 manifest.json');p.add_argument('--status',action='store_true')
    p.add_argument('--output-dir',default=str(ROOT/'outputs'));p.add_argument('--title')
    p.add_argument('--quark-home',default=os.environ.get('QUARK_SKILL_HOME'));p.add_argument('--download-home',default=os.environ.get('VIDEO_DOWNLOAD_HOME'))
    p.add_argument('--session-input-file',help='UTF-8 原始用户请求（供夸克服务质量追踪）')
    p.add_argument('--session-id',help='调用方持久保存的同一对话会话标识；只用于新批次')
    p.add_argument('--wait',type=float,default=0,help='当前进程最长复查秒数；默认单轮，持久状态可恢复')
    p.add_argument('--force','--no-cache',dest='refresh',action='store_true',help='重新请求正文，复用已上传文件')
    p.add_argument('--reformat',action='store_true',help='仅从缓存原文重建预整理')
    p.add_argument('--retry-errors',action='store_true',help='明确重试读取/授权等错误；上传仍不盲目重传')
    p.add_argument('--accept-response',metavar='REVIEW_NOTE',help='人工检查后接纳候选正文，仅允许单条任务')
    p.add_argument('--doctor',action='store_true');p.add_argument('--doctor-live',metavar='WECHAT_URL')
    for flag in ('keep-video','keep-audio','no-daemon'):p.add_argument('--'+flag,action='store_true',help='兼容旧参数；云端模式保留媒体且没有本地daemon')
    p.add_argument('--no-save',action='store_true');p.add_argument('--speakers',action='store_true');p.add_argument('--host');p.add_argument('--guest')
    args=p.parse_args(argv)
    if args.doctor:return doctor(args)
    if args.doctor_live:
        from sph_resolver import resolve_wechat
        try:
            value=resolve_wechat(args.doctor_live);print(json.dumps({'ok':bool(value),'scope':'仅解析探活，不转写'},ensure_ascii=False));return 0 if value else 1
        except Exception as e:print(str(e),file=sys.stderr);return 1
    if args.speakers or args.host or args.guest:p.error('夸克版不支持可靠说话人分离或姓名映射；未启动本地识别')
    if args.no_save:p.error('云端任务需要持久状态以避免错配与重复上传，不支持 --no-save')
    if args.wait<0:p.error('--wait 不能为负数')
    if args.session_id and not re.fullmatch(r'\d+-[A-Za-z0-9]{6}',args.session_id):p.error('--session-id 格式必须为时间戳-六位随机字符')
    if sum(bool(v) for v in (args.input,args.batch,args.resume))!=1:p.error('input、--batch、--resume 必须且只能选择一个')
    try:
        if args.resume:
            manifest=Path(args.resume).resolve();batch=read(manifest);output=Path(batch['output_dir'])
            keys=batch['asset_ids']
        else:
            output=Path(args.output_dir).expanduser().resolve()
            specs=read(args.batch) if args.batch else [{'input':args.input,'title':args.title or ''}]
            if not isinstance(specs,list) or not specs:p.error('--batch 必须是非空数组')
            pipe=Pipeline(output,None,args.download_home);keys=list(dict.fromkeys(pipe.register(s) for s in specs))
            bid=hashlib.sha256('|'.join(sorted(keys)).encode()).hexdigest()[:20]
            manifest=pipe.store/'batches'/bid/'manifest.json'
            with locked(manifest.with_suffix('.lock')):
                if manifest.exists():batch=read(manifest)
                else:
                    batch={'schema_version':1,'batch_id':bid,'output_dir':str(output),'asset_ids':keys,
                           'session_id':args.session_id or str(int(time.time()))+'-'+secrets.token_hex(3),'created_at':time.time()}
                    atomic(manifest,batch)
        if args.accept_response and len(keys)!=1:p.error('--accept-response 仅允许单条任务')
        # Original request is not invented if absent. Runtime-only; not cached as account data.
        session_input=Path(args.session_input_file).read_text() if args.session_input_file else None
        client=None if args.status or args.reformat or args.accept_response else Quark(locate('quarkclouddrive','scripts/quark-drive.cjs',args.quark_home),batch['session_id'],session_input)
        pipe=Pipeline(output,client,args.download_home);deadline=time.monotonic()+args.wait;first=True
        while True:
            states=[]
            for key in keys:
                if args.status:s=read(pipe.store/'items'/key/'state.json')
                else:
                    try:s=pipe.tick(key,refresh=args.refresh and first,reformat=args.reformat,retry_errors=args.retry_errors and first,accept_note=args.accept_response)
                    except RuntimeError as e:s={'asset_id':key,'status':'busy','error':str(e)}
                states.append(s)
                print(json.dumps({'asset_id':key,'title':s.get('title'),'status':s['status'],'next_poll_at':s.get('next_poll_at'),'error':s.get('error')},ensure_ascii=False),file=sys.stderr,flush=True)
            summary={'schema_version':1,'batch_manifest':str(manifest),'items':[{k:s[k] for k in ('asset_id','title','status','outputs','polish_status','error','next_poll_at') if k in s} for s in states],
                     'counts':{'text_received':sum(bool(s.get('raw_path')) for s in states),'waiting':sum(s['status']=='waiting_analysis' for s in states),'needs_attention':sum(s['status'] not in ('text_received','waiting_analysis') for s in states)},
                     'full_verbatim_verified':False}
            atomic(manifest.with_name('status.json'),summary)
            pending=[s for s in states if s['status']=='waiting_analysis']
            if args.status or args.reformat or args.accept_response or not pending or time.monotonic()>=deadline:break
            first=False
            until=min(s.get('next_poll_at',time.time()+60) for s in pending)-time.time()
            nap=min(max(1,until),30,max(0,deadline-time.monotonic()))
            print('[等待云端] 状态已保存；不启动本地识别。',file=sys.stderr,flush=True);time.sleep(nap)
        print('----- VT_OUTPUTS -----',file=sys.stderr)
        if len(states)==1 and states[0].get('outputs'):summary.update(states[0]['outputs'])
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0 if all(s['status']=='text_received' for s in states) else 2 if all(s['status'] in ('text_received','waiting_analysis') for s in states) else 1
    except (OSError,ValueError,RuntimeError,KeyError) as e:
        print('[ERROR] '+str(e),file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
