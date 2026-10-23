import json,time
from experiment_target import experiment_target
from provider_comparison_target import two_providers
from test_assets import seeded_run
from test_pipeline import invoke


def status_during_arm_transition(browser, monkeypatch, baseline_done, release_baseline, next_arm, candidate, release_candidate):
    """Schedule the SDK worker's completed-arm update during HTTP serialization."""
    import fastapi.encoders

    assert baseline_done.wait(40), 'Baseline did not finish its SDK experiment'
    encode = fastapi.encoders.jsonable_encoder
    paused = False

    def pause_at_row(value, *args, **kwargs):
        nonlocal paused
        if not paused and isinstance(value, dict) and 'case_id' in value and 'judge_results' in value:
            paused = True
            release_baseline.set()
            assert next_arm.wait(10), 'Worker did not advance after the baseline finished'
        return encode(value, *args, **kwargs)

    try:
        with monkeypatch.context() as scheduler:
            scheduler.setattr(fastapi.encoders, 'jsonable_encoder', pause_at_row)
            response = browser.get('/manage/experiments/status')
        assert response.status_code == 200, response.text
        assert paused, 'The response did not exercise nested experiment serialization'
        frozen = response.json()['runs'][-1]
        assert len(frozen['arms']['baseline']['rows']) == 16
        assert 'url' not in frozen['arms']['baseline']
        assert frozen['arms']['candidate']['rows'] == []
        # Native async clients now share the Companion loop. Let the encoder
        # release it before waiting for the candidate's actual provider request.
        assert candidate.wait(10), 'Candidate did not dispatch after serialization'
        advanced = browser.get('/manage/experiments/status').json()['runs'][-1]
        assert 'url' in advanced['arms']['baseline']
        assert len(advanced['arms']['candidate']['rows']) == 1
    finally:
        release_baseline.set()
        release_candidate.set()


def pause_provider_transition(target, monkeypatch):
    from threading import Event
    from langfuse._client.datasets import DatasetClient

    baseline_done, release_baseline, next_arm, candidate, release_candidate = (Event() for _ in range(5))
    run_experiment = DatasetClient.run_experiment
    started = 0

    def sdk_boundary(client, *args, **kwargs):
        nonlocal started
        started += 1
        ordinal = started
        if ordinal == 2:
            next_arm.set()
        result = run_experiment(client, *args, **kwargs)
        if ordinal == 1:
            baseline_done.set()
            assert release_baseline.wait(20), 'Completed baseline was not released'
        return result

    monkeypatch.setattr(DatasetClient, 'run_experiment', sdk_boundary)
    extension = target['extension']

    def gated(method, path, query, body):
        if path.endswith('/chat/completions') and not candidate.is_set():
            candidate.set()
            assert release_candidate.wait(20), 'First candidate call was not released'
        return extension(method, path, query, body)

    target['extension'] = gated
    return baseline_done, release_baseline, next_arm, candidate, release_candidate

def test_delivered_provider_comparison_freezes_inputs_and_attributes_both_bindings(tmp_path,monkeypatch,request):
    from sdk_identifiers import numeric_span_ids
    numeric_span_ids(monkeypatch)
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app,REQUIRES_SECRETS
    from northwind import config
    with experiment_target() as (env,target):
        seeded_run(tmp_path,env,target)
        assert invoke(tmp_path,'prepare-cohorts',extra_env=env).returncode==0
        two_providers(env,target,monkeypatch)
        transition = pause_provider_transition(target, monkeypatch)
        request.addfinalizer(lambda: (transition[1].set(), transition[4].set()))
        for k,v in env.items():monkeypatch.setenv(k,v)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_PROOF','a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS',json.dumps([{'path':'/manage/experiments','access':'manage'}]))
        (tmp_path/'state').chmod(0o555);request.addfinalizer(lambda:(tmp_path/'state').chmod(0o700))
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL,langfuse_factory=config.langfuse_factory,
            native_llm_factory=config.native_llm_factory,role_factories={'comparison':config.native_llm_factory})
        with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as browser:
            assert browser.get('/manage/experiments/providers').status_code==404
            browser.headers['x-depot-live-proof']='a'*64
            page=browser.get('/manage/experiments/providers')
            assert page.status_code==200,page.text
            assert 'Provider comparison' in page.text
            prepared=browser.post('/manage/experiments/prepare',json={}).json()
            body=dict(request_id='providers-one',cohort='english',prompt_version=prepared['control']['version'],judge_model='fixture-judge',cost_acknowledged=True)
            start=browser.post('/manage/experiments/providers/start',json=body)
            assert start.status_code==202,start.text
            status_during_arm_transition(browser, monkeypatch, *transition)
            def wait():
                deadline=time.monotonic()+40
                while time.monotonic()<deadline:
                    run=browser.get('/manage/experiments/status').json()['runs'][-1]
                    if run['status']!='running':return run
                    time.sleep(.05)
                raise AssertionError('Provider comparison did not finish')
            run=wait();assert run['status']=='awaiting_readback',run
            assert run['kind']=='provider'
            assert len(target['openai_calls'])==32 and len(target['calls'])==192
            assert all(r['model']=='gpt-4.1-mini' for r in target['openai_calls'])
            assert sum(p['provider']=='openai' for p in target['permits'])==32
            refreshed=browser.post('/manage/experiments/'+run['id']+'/readback',json={})
            assert refreshed.status_code==200,refreshed.text
            measured=refreshed.json()
            assert measured['gate']['status']=='passed',measured['gate']
            assert measured['gate']['promotion_eligible'] is False
            assert measured['scope']['prompts']['baseline']==measured['scope']['prompts']['candidate']
            assert measured['scope']['bindings']['candidate']=={'provider':'openai','model':'gpt-4.1-mini','role':'comparison'}
            assert measured['scope']['judge']=={'provider':'anthropic','model':'fixture-judge','role':'primary'}
            summary=measured['measurements']
            assert summary['candidate']['application']['usage']=={'input':480,'output':320}
            assert abs(summary['candidate']['application']['native_cost_usd']-0.064)<1e-8
            assert summary['candidate']['judges']['generations']==80
            assert len({r['trace_id'] for arm in measured['arms'].values() for r in arm['rows']})==32
            assert browser.post('/manage/experiments/providers/start',json=body).json()['id']==run['id']
            assert len(target['openai_calls'])==32
            for kind in ('application','judge'):
                target['missing_generation']=kind
                partial=browser.post('/manage/experiments/'+run['id']+'/readback',json={}).json()
                assert partial['gate']['status']=='incomplete'
                account='application' if kind=='application' else 'judges'
                for arm in partial['measurements'].values():
                    assert arm[account]['native_cost_usd'] is None, arm[account]
                    assert arm[account]['usage'] is None and arm[account]['missing']>0
            target.pop('missing_generation')
            target['missing_cost']=True
            assert browser.post('/manage/experiments/'+run['id']+'/readback',json={}).json()['gate']['status']=='incomplete'

            target.pop('missing_cost')
            target['missing_model']=True
            assert browser.post('/manage/experiments/providers/start',json={**body,'request_id':'missing-model'}).status_code==202
            failed=wait()
            assert failed['status']=='runner_failed'
            assert len(failed['arms']['baseline']['rows'])==16
            assert failed['arms']['candidate']['rows'][0]['provider_error']=='OpenAIModelNotFoundError'
            assert failed['arms']['candidate']['rows'][0]['model']=='gpt-4.1-mini'
            assert len(target['openai_calls'])==33,'Unavailable model was retried or substituted'
            failed=browser.post('/manage/experiments/'+failed['id']+'/readback',json={}).json()
            assert failed['measurements']['candidate']['application']['native_cost_usd'] is None
            assert failed['measurements']['candidate']['application']['usage'] is None
            target.pop('missing_model');target['deny_openai']=True
            assert browser.post('/manage/experiments/providers/start',json={**body,'request_id':'denied-provider'}).status_code==202
            denied=wait()
            assert denied['status']=='runner_failed' and len(target['openai_calls'])==33
            assert denied['arms']['candidate']['rows'][0]['provider_error']=='SpendingDenied'
            target.pop('deny_openai');target['missing_usage']=True
            assert browser.post('/manage/experiments/providers/start',json={**body,'request_id':'missing-usage'}).status_code==202
            missing=wait()
            missing=browser.post('/manage/experiments/'+missing['id']+'/readback',json={}).json()
            assert missing['gate']['status']=='incomplete'
            assert all(r['usage'] is None for r in missing['arms']['candidate']['rows'])
            assert missing['measurements']['candidate']['application']['usage'] is None
            target.pop('missing_usage')
            assert browser.post('/manage/experiments/providers/start',json={**body,'cohort':'disputes','request_id':'two-turn-disputes'}).status_code==202
            disputes=wait()
            disputes=browser.post('/manage/experiments/'+disputes['id']+'/readback',json={}).json()
            assert disputes['gate']['status']=='passed',disputes['gate']
            assert all(r['turns']==2 for a in disputes['arms'].values() for r in a['rows'])
            assert disputes['measurements']['candidate']['application']['generations']==50
            assert 'fixture-openai-secret' not in json.dumps(browser.get('/manage/experiments/status').json())


def test_disabled_comparison_resolves_no_extra_provider_or_paid_work(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app,REQUIRES_SECRETS
    from northwind import config
    from experiment_target import provider_fixture
    with experiment_target() as (env,target):
        provider_fixture(env,target,monkeypatch)
        for k,v in env.items():monkeypatch.setenv(k,v)
        monkeypatch.delenv('OPENAI_API_KEY',raising=False);monkeypatch.delenv('LLM_ROLES_JSON',raising=False)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path))
        monkeypatch.setenv('PORTAL_LIVE_PROOF','a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS',json.dumps([{'path':'/manage/experiments','access':'manage'}]))
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL,langfuse_factory=config.langfuse_factory,
            native_llm_factory=config.native_llm_factory,role_factories={'comparison':config.native_llm_factory})
        with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as browser:
            browser.headers['x-depot-live-proof']='a'*64
            state=browser.get('/manage/experiments/providers/status').json()
            assert state=={'enabled':False,'reason':'OpenAI comparison is disabled for this deployment'}
            response=browser.post('/manage/experiments/providers/start',json=dict(request_id='off',cohort='english',prompt_version=1,judge_model='fixture-judge',cost_acknowledged=True))
            assert response.status_code==409 and target['calls']==target['permits']==[]
            assert browser.post('/manage/experiments/providers/start',headers={'origin':'http://foreign.invalid'},json={}).status_code==403
