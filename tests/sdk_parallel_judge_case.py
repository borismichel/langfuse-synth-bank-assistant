"""An invalid external judge result stops the delivered comparison without retry."""
import json
from test_assets import seeded_run
from test_pipeline import invoke

def test_two_judges_share_one_serial_row_and_cancel_without_orphan_calls(tmp_path,monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app, REQUIRES_SECRETS
    from northwind import config
    from experiment_target import experiment_target,provider_fixture
    with experiment_target() as (env,target):
        seeded_run(tmp_path,env,target)
        assert invoke(tmp_path,'prepare-cohorts',extra_env=env).returncode==0
        provider_fixture(env,target,monkeypatch)
        from threading import Event, Lock
        first, second, third, release_first, release_rest = (Event() for _ in range(5))
        lock=Lock(); seen=[]
        original=target['extension']
        def external(method,path,query,body):
            result=original(method,path,query,body)
            if path.endswith('/messages') and body['model']=='fixture-judge':
                with lock:
                    seen.append(body);number=len(seen)
                (first if number==1 else second if number==2 else third).set()
                assert (release_first if number==1 else release_rest).wait(10), 'Fixture release missing'
            return result
        target['extension']=external
        for key,value in env.items():monkeypatch.setenv(key,value)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_PROOF','a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS',json.dumps([{'path':'/manage/experiments','access':'manage'}]))
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL,langfuse_factory=config.langfuse_factory,native_llm_factory=config.native_llm_factory)
        with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as browser:
            browser.headers['x-depot-live-proof']='a'*64
            assert browser.post('/manage/experiments/prepare',json={}).status_code==200
            body=dict(request_id='invalid-judge',cohort='english',baseline_version=1,candidate_version=1,
                      candidate_policy='current',judge_model='fixture-judge',cost_acknowledged=True)
            try:
                started=browser.post('/manage/experiments/start',json=body)
                assert started.status_code==202
                assert first.wait(5) and second.wait(2), 'Two policies must reach real provider HTTP concurrently'
                assert len(target['calls'])==4 and not third.is_set(), 'At most two judges; no next application row'
                release_first.set()
                assert third.wait(3), 'An available judge slot must take the next policy'
                assert len(target['calls'])==5
                assert browser.post('/manage/experiments/cancel',json={}).status_code==200
                release_rest.set()
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    run=browser.get('/manage/experiments/status').json()['runs'][0]
                    if run['status']!='running':break
                    time.sleep(.02)
                assert run['status']=='cancelled' and run['gate']['status']!='passed',run
                assert run['dispatches']==len(target['calls'])==5
                assert len(run['arms']['baseline']['rows'])==1 and not run['arms']['candidate']['rows']
                row=run['arms']['baseline']['rows'][0]
                assert row['judge_results']['P1']['usage']['output']==12
                assert set(row['judge_results'])=={'P1','P2','P3'}
                assert all(r['generation_id'] and r['generation_trace_id']==row['trace_id'] for r in row['judge_results'].values())
                assert run['limits']['concurrency']==1 and run['limits']['judge_concurrency']==2
                assert browser.post('/manage/experiments/start',json=body).json()==run
                assert len(target['calls'])==5
            finally:
                browser.post('/manage/experiments/cancel',json={});release_first.set();release_rest.set()
