import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import quark_transcript as q
import make_optimized as render

class Fake:
    def __init__(self,name,size,duration=60,mode='normal'):
        self.calls=[];self.name=name;self.size=size;self.duration=duration;self.mode=mode
        self.text='开头内容。'+('这是需要完整保留的中间内容，数字123和专有名词。'*25)+'最后一句。'
    def call(self,action,args,directory):
        self.calls.append(action);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        rows=[];result={'code':0,'data':{},'msg':'成功'}
        if action=='upload':
            if self.mode=='unknown_upload':result={'code':'OUTCOME_UNKNOWN','msg':'timeout'}
            elif self.mode=='auth_upload':result={'code':-1,'msg':'未授权'}
            else:rows=[{'type':'list','code':0,'data':{'fileName':self.name,'fileSize':self.size,'fileId':'raw-id','recordId':'record'}}]
        if action=='search':
            records=[{'filename':self.name,'size':self.size,'duration':self.duration,'created_at':int(time.time()*1000),'fid':'opaque|cloud-id'}]
            if self.mode=='collision':records.append(dict(records[0],fid='opaque|other'))
            p=directory/'artifact.jsonl';p.write_text('\n'.join(json.dumps(r) for r in records))
            rows=[{'type':'artifact','data':{'file_path':str(p)}}]
        if action=='qa':
            if self.mode=='pending':result={'code':-1505,'msg':'分析中'}
            elif self.mode=='auth':result={'code':-1,'msg':'认证 token 过期'}
            else:
                text=self.text+'\n\n**以上内容参考1项网盘文件**：\n1. '+self.name
                if self.mode=='truncated':text='以下是摘要。\n'+self.name
                result={'code':0,'data':{'task_id':'task','text_block':{'text':text}}}
        log=directory/'response.jsonl';log.write_text(json.dumps(result))
        return {'rows':rows,'result':result,'log':str(log)}

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.media=self.root/'same.mp4';self.media.write_bytes(b'original bytes')
        self.pipe=q.Pipeline(self.root/'out',None);self.key=self.pipe.register(str(self.media));self.d=self.pipe.store/'items'/self.key
        s=q.read(self.d/'state.json');s.update(upload_path=str(self.media),local_path=str(self.media),expected_name='vt-'+self.key+'.mp4',duration=60,size=self.media.stat().st_size,has_video=True,status='downloaded');q.atomic(self.d/'state.json',s)
        self.fake=Fake(s['expected_name'],s['size']);self.pipe.client=self.fake
    def tearDown(self):self.tmp.cleanup()
    def test_cloud_roundtrip_and_render_preserve_text_without_timestamps(self):
        s=self.pipe.tick(self.key);self.assertEqual(s['status'],'text_received')
        content=render.parse_optimized_md(Path(s['outputs']['preorganized_path']).read_text())
        joined=''.join(p for sec in content['sections'] for p in sec['paras'])
        self.assertEqual(joined,self.fake.text);self.assertFalse(any(sec.get('start') for sec in content['sections']))
        md=render.build_md(content);html=render.build_html(content,md,'out.md')
        self.assertIn(q.ENGINE,md);self.assertNotIn('FunASR',md);self.assertNotIn('[None',html)
    def test_new_process_resume_does_not_upload_or_query_again(self):
        self.pipe.tick(self.key);calls=list(self.fake.calls)
        q.Pipeline(self.root/'out',self.fake).tick(self.key)
        self.assertEqual(self.fake.calls,calls)
    def test_same_filename_different_content_never_collapses(self):
        other=self.root/'other';other.mkdir();f=other/'same.mp4';f.write_bytes(b'different bytes')
        self.assertNotEqual(self.pipe.register(str(f)),self.key)
    def test_identical_bytes_deduplicate_and_preserve_aliases(self):
        f=self.root/'renamed.mp4';f.write_bytes(self.media.read_bytes());self.assertEqual(self.pipe.register(str(f)),self.key)
        self.assertIn(str(f),q.read(self.d/'state.json')['sources'])
    def test_douyin_modal_and_video_link_share_identity(self):
        a=self.pipe.register('https://www.douyin.com/jingxuan?modal_id=123')
        b=self.pipe.register('https://www.douyin.com/video/123');self.assertEqual(a,b)
    def test_unknown_upload_reconciles_without_second_upload(self):
        self.fake.mode='unknown_upload';s=self.pipe.tick(self.key);self.assertEqual(s['status'],'text_received')
        self.pipe.tick(self.key);self.assertEqual(self.fake.calls.count('upload'),1)
    def test_crash_after_upload_intent_only_searches(self):
        s=q.read(self.d/'state.json');s.update(upload_started_at=time.time(),status='upload_outcome_unknown',attempts=[{'action':'upload'}]);q.atomic(self.d/'state.json',s)
        self.pipe.tick(self.key);self.assertNotIn('upload',self.fake.calls)
    def test_same_name_ambiguous_cloud_results_stop_qa(self):
        self.fake.mode='collision';s=self.pipe.tick(self.key);self.assertEqual(s['status'],'mapping_needs_review');self.assertNotIn('qa',self.fake.calls)
        self.pipe.tick(self.key);self.assertEqual(self.fake.calls.count('upload'),1)
    def test_pending_is_durable_and_does_not_busy_poll(self):
        self.fake.mode='pending';s=self.pipe.tick(self.key);self.assertEqual(s['status'],'waiting_analysis');calls=len(self.fake.calls)
        self.pipe.tick(self.key);self.assertEqual(len(self.fake.calls),calls)
        self.fake.mode='normal';s=self.pipe.tick(self.key,refresh=True);self.assertEqual(s['status'],'text_received');self.assertEqual(self.fake.calls.count('upload'),1)
    def test_auth_failure_stops_until_explicit_retry(self):
        self.fake.mode='auth';self.assertEqual(self.pipe.tick(self.key)['status'],'auth_required');calls=len(self.fake.calls)
        self.pipe.tick(self.key);self.assertEqual(len(self.fake.calls),calls)
        self.fake.mode='normal';self.assertEqual(self.pipe.tick(self.key,retry_errors=True)['status'],'text_received')
    def test_rejected_upload_can_retry_after_login_without_confusing_unknown(self):
        self.fake.mode='auth_upload';self.assertEqual(self.pipe.tick(self.key)['status'],'auth_required')
        self.fake.mode='normal';self.assertEqual(self.pipe.tick(self.key,retry_errors=True)['status'],'text_received')
        self.assertEqual(self.fake.calls.count('upload'),2)
    def test_truncation_is_not_delivered_as_full_text(self):
        self.fake.mode='truncated';s=self.pipe.tick(self.key);self.assertEqual(s['status'],'response_needs_review');self.assertNotIn('outputs',s)
    def test_refresh_bad_answer_keeps_previous_raw(self):
        s=self.pipe.tick(self.key);old=s['raw_path'];self.fake.mode='truncated'
        s=self.pipe.tick(self.key,refresh=True);self.assertEqual(s['raw_path'],old);self.assertEqual(s['status'],'response_needs_review')
    def test_reformat_does_not_network(self):
        self.pipe.tick(self.key);self.fake.call=lambda *a: self.fail('network in reformat')
        self.assertTrue(self.pipe.tick(self.key,reformat=True)['outputs'])
    def test_lock_stops_duplicate_execution(self):
        with q.locked(self.d/'task.lock'):
            with self.assertRaisesRegex(RuntimeError,'TASK_BUSY'):self.pipe.tick(self.key)
        self.assertEqual(self.fake.calls,[])
    def test_tampered_upload_copy_is_not_sent(self):
        self.media.write_bytes(b'changed');s=self.pipe.tick(self.key);self.assertEqual(s['status'],'failed');self.assertEqual(self.fake.calls,[])
    def test_cloud_input_selects_exact_fid_among_same_name(self):
        key=self.pipe.register({'input':'quark:opaque|cloud-id','filename':self.fake.name,'size':self.fake.size,'duration':60})
        self.fake.mode='collision';s=self.pipe.tick(key);self.assertEqual(s['status'],'text_received');self.assertNotIn('upload',self.fake.calls)
    def test_legacy_timestamp_renderer_still_works(self):
        md='# Old\n\n> 转录: FunASR(SenseVoice-Small) 2026-01-01\n\n## 1. Section [00:00 - 01:00]\n\nPreserved.\n'
        c=render.parse_optimized_md(md);self.assertEqual(c['sections'][0]['start'],'00:00');self.assertIn('[00:00 - 01:00]',render.build_md(c))
    def test_cloud_missing_duration_is_recorded_without_false_verification(self):
        original=self.fake.call
        def call(action,args,directory):
            response=original(action,args,directory)
            if action=='search':
                path=Path(response['rows'][0]['data']['file_path']);row=json.loads(path.read_text());row.pop('duration');path.write_text(json.dumps(row))
            return response
        self.fake.call=call;s=self.pipe.tick(self.key)
        self.assertEqual(s['status'],'text_received');self.assertFalse(s['duration_readback_verified'])
    def test_refreshed_opaque_fid_uses_stable_official_tail(self):
        key=self.pipe.register({'input':'quark:older-wrapper|cloud-id','filename':self.fake.name,'size':self.fake.size,'duration':60})
        s=self.pipe.tick(key);self.assertEqual(s['status'],'text_received');self.assertEqual(s['qa_fid'],'opaque|cloud-id')
    def test_cloud_reissued_fid_deduplicates(self):
        spec={'filename':self.fake.name,'size':self.fake.size}
        a=self.pipe.register(dict(spec,input='quark:old|stable'))
        b=self.pipe.register(dict(spec,input='quark:new|stable'));self.assertEqual(a,b)
    def test_refresh_pending_can_continue_with_previous_raw_retained(self):
        previous=self.pipe.tick(self.key)['raw_path'];self.fake.mode='pending'
        s=self.pipe.tick(self.key,refresh=True);self.assertEqual(s['raw_path'],previous)
        s['next_poll_at']=0;q.atomic(self.d/'state.json',s);self.fake.mode='normal'
        s=self.pipe.tick(self.key);self.assertEqual(s['status'],'text_received');self.assertNotEqual(s['raw_path'],previous)
    def test_scope_disclaimer_is_flagged_and_not_mixed_into_body(self):
        raw=self.fake.text+'\n\n**可见范围说明：**\n无法确认是否覆盖全部。'
        body,quality=q.response_check(raw,self.fake.name,60)
        self.assertEqual(body,self.fake.text);self.assertIn('scope_limit_declared',quality['flags'])
    def test_no_local_models_loaded(self):
        self.pipe.tick(self.key)
        for module in ['funasr','torch','torchaudio','diarize_asr','asr_daemon']:self.assertNotIn(module,sys.modules)

    def test_download_failed_receipt_cannot_adopt_existing_file(self):
        key=self.pipe.register('https://www.douyin.com/video/991')
        d=self.pipe.store/'items'/key;s=q.read(d/'state.json')
        import subprocess
        reply=subprocess.CompletedProcess([],1,stdout=json.dumps({'ok':False,'path':str(self.media)}),stderr='failed')
        with patch.object(q,'locate',return_value=self.root),patch.object(q.subprocess,'run',return_value=reply):
            with self.assertRaisesRegex(RuntimeError,'DOWNLOAD_FAILED'):self.pipe.acquire(s,d)
        self.assertNotIn('upload_path',s)
    def test_download_path_outside_asset_is_rejected(self):
        key=self.pipe.register('https://www.douyin.com/video/992')
        d=self.pipe.store/'items'/key;s=q.read(d/'state.json')
        import subprocess
        reply=subprocess.CompletedProcess([],0,stdout=json.dumps({'ok':True,'path':str(self.media)}),stderr='')
        with patch.object(q,'locate',return_value=self.root),patch.object(q.subprocess,'run',return_value=reply):
            with self.assertRaisesRegex(RuntimeError,'DOWNLOAD_PATH_UNBOUND'):self.pipe.acquire(s,d)
    def test_media_without_audio_remains_usable_for_visuals(self):
        import subprocess
        reply=subprocess.CompletedProcess([],0,stdout=json.dumps({'format':{'duration':'2'},'streams':[{'codec_type':'video'}]}),stderr='')
        with patch.object(q.subprocess,'run',return_value=reply):
            info=q.media_info(self.media)
        self.assertTrue(info['has_video']);self.assertFalse(info['has_audio'])

if __name__=='__main__':unittest.main()
