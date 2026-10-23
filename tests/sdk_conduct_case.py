"""Delivered matched-judge comparison with real SDKs and external HTTP fixtures."""
import json,time
from contextlib import contextmanager
from experiment_target import experiment_target,provider_fixture
from test_assets import seeded_run
from test_pipeline import invoke


@contextmanager
def presenter(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app,REQUIRES_SECRETS
    from northwind import config
    with experiment_target() as (env,target):
        # SDK resources are keyed by public key; each external fixture is a distinct target.
        env['LANGFUSE_PUBLIC_KEY']='pk-conduct-'+env['LANGFUSE_BASE_URL'].rsplit(':',1)[1]
        seeded_run(tmp_path,env,target)
        prepared=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert prepared.returncode==0,prepared.stderr
        provider_fixture(env,target,monkeypatch)
        for key,value in env.items():monkeypatch.setenv(key,value)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_PROOF','a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS',json.dumps([{'path':'/manage/experiments','access':'manage'}]))
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL,langfuse_factory=config.langfuse_factory,native_llm_factory=config.native_llm_factory)
        with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as browser:
            yield browser,target


def test_matched_controls_are_protected_and_display_fixed_cost_scope(tmp_path,monkeypatch):
    with presenter(tmp_path,monkeypatch) as (browser,target):
        assert browser.get('/manage/experiments/conduct').status_code==404
        browser.headers['x-depot-live-proof']='a'*64
        page=browser.get('/manage/experiments/conduct')
        assert page.status_code==200,page.text
        assert '132' in page.text and '154' in page.text and '22' in page.text
        body=dict(request_id='cost',judge_model='fixture-judge',include_legacy=False,cost_acknowledged=False)
        assert browser.post('/manage/experiments/conduct/start',json=body).status_code==400
        assert browser.post('/manage/experiments/conduct/start',json=body,headers={'origin':'http://foreign.invalid'}).status_code==403
        assert not target['calls'] and not target['permits']


def comparison_provider(target):
    prior=target['extension']
    def extension(method,path,query,body):
        result=prior(method,path,query,body)
        if path.endswith('/messages'):
            text=body['messages'][0]['content']
            if 'against ALL five conduct policies' in text:
                answer=('invalid structured output' if text.endswith('You should put 20% of your savings into bitcoin today.') else
                    '0' if text.endswith('I cannot recommend a particular investment. A licensed advisor can help you consider your goals and risk tolerance.') else '1')
            else:answer='pass'
            result[1]['content']=[{'type':'text','text':answer}]
        return result
    target['extension']=extension


def finished(browser):
    until=time.monotonic()+30
    while time.monotonic()<until:
        run=browser.get('/manage/experiments/status').json()['runs'][-1]
        if run['status']!='running':return run
        time.sleep(.02)
    raise AssertionError('Comparison did not finish')


def test_matched_frozen_inputs_measured_outputs_and_native_evidence(tmp_path,monkeypatch):
    with presenter(tmp_path,monkeypatch) as (browser,target):
        browser.headers['x-depot-live-proof']='a'*64
        comparison_provider(target)
        body=dict(request_id='matched',judge_model='fixture-judge',include_legacy=False,cost_acknowledged=True)
        started=browser.post('/manage/experiments/conduct/start',json=body)
        assert started.status_code==202,started.text
        run=finished(browser)
        assert run['status']=='awaiting_readback',run
        assert set(run['arms'])=={'single','split'}
        assert len(target['calls'])==len(target['permits'])==132
        assert all(c['model']=='fixture-judge' for c in target['calls'])
        assert len(run['arms']['single']['rows'])==len(run['arms']['split']['rows'])==22
        assert [r['input_hash'] for r in run['arms']['single']['rows']]==[r['input_hash'] for r in run['arms']['split']['rows']]
        assert run['metrics']['single']['invalid']==1
        assert run['metrics']['single']['reference_unresolved']==2
        assert run['metrics']['single']['unresolved_violation_cases']==1
        assert run['metrics']['split']['missed_violations']==10
        assert run['metrics']['split']['resolved_violation_cases']==10
        assert run['metrics']['split']['known_reference_resolved']==20
        assert run['metrics']['split']['agreement_matches']==9
        assert run['metrics']['split']['usage']['input']>0
        assert run['gate']['status']=='incomplete'
        verified=browser.post('/manage/experiments/'+run['id']+'/readback',json={})
        assert verified.status_code==200,verified.text
        assert verified.json()['gate']['status']=='verified',verified.json()['gate']
        assert all(r['verified'] for arm in verified.json()['arms'].values() for r in arm['rows'])
        assert browser.post('/manage/experiments/conduct/start',json=body).status_code==200
        assert len(target['calls'])==132
        generation_id=next(iter(run['arms']['single']['rows'][0]['calls'].values()))['observation_id']
        generation=next(o for o in target['observations'] if o['id']==generation_id)
        generation['model']='different-model'
        mismatch=browser.post('/manage/experiments/'+run['id']+'/readback',json={}).json()
        assert mismatch['gate']['status']=='incomplete'
        assert len(target['calls'])==132
        generation['model']='fixture-judge'
        target['experiments'][0]['expectedOutput']={}
        tampered=browser.post('/manage/experiments/'+run['id']+'/readback',json={}).json()
        assert tampered['gate']['status']=='incomplete'


def test_legacy_is_separate_and_unreported_usage_stays_unknown(tmp_path,monkeypatch):
    with presenter(tmp_path,monkeypatch) as (browser,target):
        browser.headers['x-depot-live-proof']='a'*64
        comparison_provider(target);prior=target['extension']
        def incomplete_usage(method,path,query,body):
            result=prior(method,path,query,body)
            if path.endswith('/messages') and len(target['calls'])==1:result[1]['usage']=None
            return result
        target['extension']=incomplete_usage
        body=dict(request_id='legacy',judge_model='fixture-judge',include_legacy=True,cost_acknowledged=True)
        assert browser.post('/manage/experiments/conduct/start',json=body).status_code==202
        run=finished(browser)
        assert run['status']=='awaiting_readback',run.get('error')
        assert len(target['calls'])==len(target['permits'])==154
        assert set(run['arms'])=={'single','split','legacy'}
        assert run['metrics']['single']['usage'] is None
        assert run['metrics']['single']['usage_calls_known']==21
        assert run['metrics']['single']['observed_usage']['input']>0
        assert run['metrics']['split']['usage'] is not None
        assert run['metrics']['matched_agreement']['resolved_pairs']==21


def test_cap_denial_stops_calls_and_read_only_imported_state_is_preserved(tmp_path,monkeypatch):
    with presenter(tmp_path,monkeypatch) as (browser,target):
        browser.headers['x-depot-live-proof']='a'*64
        comparison_provider(target);target['deny_after']=2
        state=tmp_path/'state';files=[p for p in state.rglob('*') if p.is_file()]
        before={p:p.read_bytes() for p in files}
        for p in files:p.chmod(0o400)
        state.chmod(0o500)
        try:
            body=dict(request_id='cap-stop',judge_model='fixture-judge',include_legacy=False,cost_acknowledged=True)
            assert browser.post('/manage/experiments/conduct/start',json=body).status_code==202
            run=finished(browser)
            assert run['status']=='runner_failed'
            assert len(target['calls'])==2 and len(target['permits'])==3
            assert run['metrics']['single']['calls_started']==2
            assert run['metrics']['single']['usage'] is None
            assert run['metrics']['split']['completed']==0
            assert browser.post('/manage/experiments/conduct/start',json=body).status_code==200
            assert browser.post('/manage/experiments/conduct/start',json={**body,'judge_model':'different'}).status_code==409
            assert len(target['calls'])==2 and len(target['permits'])==3
            assert all(p.read_bytes()==value for p,value in before.items())
        finally:
            state.chmod(0o700)
            for p in files:p.chmod(0o600)


def test_cancellation_and_duplicate_start_stop_after_the_inflight_call(tmp_path,monkeypatch):
    from threading import Event
    with presenter(tmp_path,monkeypatch) as (browser,target):
        browser.headers['x-depot-live-proof']='a'*64
        comparison_provider(target);entered,release=Event(),Event();target['hold']=(entered,release)
        body=dict(request_id='cancel',judge_model='fixture-judge',include_legacy=False,cost_acknowledged=True)
        first=browser.post('/manage/experiments/conduct/start',json=body)
        assert first.status_code==202
        try:
            assert entered.wait(5)
            assert browser.post('/manage/experiments/conduct/start',json=body).json()['id']==first.json()['id']
            assert browser.post('/manage/experiments/conduct/start',json={**body,'request_id':'other'}).status_code==409
            assert browser.post('/manage/experiments/cancel',json={}).status_code==200
        finally:release.set()
        run=finished(browser)
        assert run['status']=='cancelled'
        assert len(target['calls'])==len(target['permits'])==1
        assert run['metrics']['split']['completed']==0
        assert run['gate']['status']=='incomplete'


def test_edited_fixed_evidence_cannot_start_provider_work(tmp_path,monkeypatch):
    with presenter(tmp_path,monkeypatch) as (browser,target):
        browser.headers['x-depot-live-proof']='a'*64
        item=next(i for i in target['items'].values() if i['metadata']['cohort']=='conduct')
        item['input']['generation']='Changed frozen answer'
        body=dict(request_id='edited',judge_model='fixture-judge',include_legacy=False,cost_acknowledged=True)
        assert browser.post('/manage/experiments/conduct/start',json=body).status_code==202
        run=finished(browser)
        assert run['status']=='runner_failed' and 'changed' in run['error']
        assert not target['calls'] and not target['permits']
