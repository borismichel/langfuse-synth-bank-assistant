"""Real persistent provider connections across live and experiment HTTP actions."""
import json
import time
from threading import Event
from experiment_target import experiment_target,provider_fixture
from test_assets import seeded_run
from test_pipeline import invoke


def test_live_answers_and_comparison_share_client_without_cross_loop_failure(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app,REQUIRES_SECRETS
    from northwind import config
    with experiment_target(keepalive=True) as (env,target):
        seeded_run(tmp_path,env,target)
        prepared=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert prepared.returncode==0,prepared.stderr
        provider_fixture(env,target,monkeypatch)
        for key,value in env.items():monkeypatch.setenv(key,value)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_PROOF','a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS',json.dumps([{'path':'/manage','access':'manage'}]))
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL,langfuse_factory=config.langfuse_factory,native_llm_factory=config.native_llm_factory)
        entered,released=Event(),Event();external=target['extension']
        def transport(method,path,query,body):
            if (method=='POST' and path.endswith('/messages') and body['model']!='fixture-judge'
                    and 'How much does an outgoing international wire cost' in str(body['messages']) and not entered.is_set()):
                entered.set()
                assert released.wait(10),'Concurrent live answer did not complete'
            return external(method,path,query,body)
        target['extension']=transport
        try:
            with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as browser:
                browser.headers['x-depot-live-proof']='a'*64
                def answer():
                    session=browser.post('/api/session').json()['session_id']
                    response=browser.post('/api/chat',json={'session_id':session,'message':'What is the late payment fee on the Classic credit card?'})
                    assert response.status_code==200,response.text
                    value=response.json()
                    assert value['prompt_version']==1
                    assert '29' in value['answer'] and 'upgrading' not in value['answer'],value
                    return value
                initial=answer()  # Opens a persistent native async client on the live loop.
                versions=browser.post('/manage/experiments/prepare',json={})
                assert versions.status_code==200,versions.text
                body=dict(request_id='loop-comparison',cohort='english',baseline_version=1,candidate_version=2,
                    candidate_policy='current',judge_model='fixture-judge',cost_acknowledged=True)
                started=browser.post('/manage/experiments/start',json=body)
                assert started.status_code==202,started.text
                assert entered.wait(5),'Comparison did not reach the provider boundary'
                concurrent=answer()  # Different prompt/context while the experiment call is held.
                released.set()
                deadline=time.monotonic()+40
                while time.monotonic()<deadline:
                    run=browser.get('/manage/experiments/status').json()['runs'][0]
                    if run['status']!='running':break
                    time.sleep(.03)
                assert run['status']=='awaiting_readback',run
                after=answer()
                assert len({r['trace_id'] for r in (initial,concurrent,after)})==3
                native=browser.post('/manage/experiments/'+run['id']+'/readback',json={})
                assert native.status_code==200,native.text
                run=native.json()
                assert run['gate']['status']=='failed',run['gate']  # Deliberate selling regression remains rejected.
                assert run['gate']['scored']==16 and run['gate']['missing']==0 and run['gate']['errors']==0
                assert all(row['verified'] for arm in run['arms'].values() for row in arm['rows'])
                assert all('upgrading' not in row['answer'] for row in run['arms']['baseline']['rows'])
                assert all('upgrading' in row['answer'] for row in run['arms']['candidate']['rows'])
        finally:released.set()
