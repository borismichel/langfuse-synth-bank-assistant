"""An invalid external judge result stops the delivered comparison without retry."""
import json
from test_assets import seeded_run
from test_pipeline import invoke

def test_invalid_judge_response_preserves_evidence_and_stops_further_dispatch(tmp_path,monkeypatch):
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
        target['judge_response']='not-an-allowed-verdict'
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
            assert browser.post('/manage/experiments/start',json=body).status_code==202
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                run=browser.get('/manage/experiments/status').json()['runs'][0]
                if run['status']!='running':break
                time.sleep(.02)
            assert run['status']=='runner_failed' and run['gate']['status']=='runner_failed',run
            assert run['dispatches']==len(target['calls'])==len(target['permits'])==3
            row=run['arms']['baseline']['rows'][0]
            assert row['error']=='judge_output_invalid' and not run['arms']['candidate']['rows']
            judged=row['judge_results']['P1']
            assert judged['output_valid'] is False and judged['verdict'] is None
            assert judged['usage']['output']==12 and 'not-an-allowed-verdict' in judged['text']
            assert judged['generation_id'] and judged['generation_trace_id']
            assert browser.post('/manage/experiments/start',json=body).json()['id']==run['id']
            assert len(target['calls'])==3
