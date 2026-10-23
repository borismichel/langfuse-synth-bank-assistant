"""Actual SDK, MCP and presenter HTTP controls against local external fixtures."""
import json
from asset_target import asset_target
from test_assets import seeded_run
from test_pipeline import invoke


def test_protected_experiment_controls_preserve_fixed_cohorts_without_spending(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app, REQUIRES_SECRETS
    from northwind import config
    with asset_target() as (env, target):
        seeded_run(tmp_path, env, target)
        result = invoke(tmp_path, 'prepare-cohorts', extra_env=env)
        assert result.returncode == 0, result.stderr
        for key, value in env.items(): monkeypatch.setenv(key, value)
        monkeypatch.setenv('SYNTH_STATE_DIR', str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_INSTANCE_ID', 'experiment-fixture')
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS', json.dumps([{'path':'/manage/experiments','access':'manage'}]))
        monkeypatch.setenv('PORTAL_LIVE_PROOF', 'a'*64)
        adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
            langfuse_factory=config.langfuse_factory, native_llm_factory=config.native_llm_factory)
        with TestClient(guarded_app(create_app(adapter), health_path='/api/health')) as browser:
            assert browser.get('/manage/experiments').status_code == 404
            browser.headers['x-depot-live-proof'] = 'a'*64
            page = browser.get('/manage/experiments')
            assert page.status_code == 200, page.text
            assert 'Application experiments' in page.text
            status = browser.get('/manage/experiments/status').json()
            assert status['scope'] == 'application-retrieval-and-mcp'
            assert {k:v['expected'] for k,v in status['cohorts'].items()} == {'english':16,'spanish':10,'redteam':10,'disputes':10}
            assert status['runs'] == []
            denied = browser.post('/manage/experiments/start', json={
                'request_id':'missing-cost', 'cohort':'english', 'baseline_version':7,
                'candidate_version':7, 'candidate_policy':'current', 'judge_model':'fixture-model'})
            assert denied.status_code == 400, denied.text
            foreign = browser.post('/manage/experiments/start', headers={'origin':'http://foreign.invalid'}, json={})
            assert foreign.status_code == 403

            assert 'test-secret' not in json.dumps(status)


def test_comparison_replays_fixed_application_cohort_and_requires_exact_native_readback(tmp_path,monkeypatch,request):
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
        result=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert result.returncode==0,result.stderr
        # Depot mounts imported history read-only; presenter writes belong elsewhere.
        state = tmp_path/'state'
        before = {p.name:p.read_bytes() for p in state.iterdir() if p.is_file()}
        state.chmod(0o555)
        request.addfinalizer(lambda: state.chmod(0o700))
        provider_fixture(env,target,monkeypatch)
        for key,value in env.items():monkeypatch.setenv(key,value)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_PROOF','a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS',json.dumps([{'path':'/manage/experiments','access':'manage'}]))
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL,langfuse_factory=config.langfuse_factory,native_llm_factory=config.native_llm_factory)
        with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as browser:
            browser.headers['x-depot-live-proof']='a'*64
            prepared=browser.post('/manage/experiments/prepare',json={})
            assert prepared.status_code==200,prepared.text
            assert prepared.json()['control']['version']==1 and prepared.json()['regression']['version']==2
            writes=len(target['writes'])
            assert browser.post('/manage/experiments/prepare',json={}).json()==prepared.json()
            assert len(target['writes'])==writes
            assert target['versions'][('northwind-assistant-system',1)]['labels']==['production']
            body=dict(request_id='comparison-one',cohort='english',baseline_version=1,candidate_version=1,
                      candidate_policy='current',judge_model='fixture-judge',cost_acknowledged=True)
            started=browser.post('/manage/experiments/start',json=body)
            assert started.status_code==202,started.text
            identity=started.json()['id']
            again=browser.post('/manage/experiments/start',json=body)
            assert again.json()['id']==identity
            assert browser.post('/manage/experiments/start',json={**body,'candidate_policy':'broad-retrieval'}).status_code==409
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                run=browser.get('/manage/experiments/status').json()['runs'][0]
                if run['status']!='running':break
                time.sleep(.05)
            assert run['status']=='awaiting_readback',json.dumps(run)
            assert len(run['arms']['baseline']['rows'])==len(run['arms']['candidate']['rows'])==16
            assert run['gate']['status']=='incomplete'
            assert all(a['url'].endswith('/experiments/results?baseline='+a['id']) for a in run['arms'].values())
            assert len(target['calls'])==len(target['permits'])==224
            assert {p.name:p.read_bytes() for p in state.iterdir() if p.is_file()} == before
            assert len({r['trace_id'] for arm in run['arms'].values() for r in arm['rows']})==32
            actual=browser.post('/manage/experiments/'+identity+'/readback',json={})
            assert actual.status_code==200,actual.text
            run=actual.json()
            assert run['gate']['status']=='passed',run['gate']
            assert all(r['verified'] for arm in run['arms'].values() for r in arm['rows'])
            calls=len(target['calls'])
            assert browser.post('/manage/experiments/start',json=body).json()['id']==identity
            assert len(target['calls'])==calls
            target['deny_read']='/api/public/experiment-items'
            denied_read=browser.post('/manage/experiments/'+identity+'/readback',json={})
            assert denied_read.status_code==409
            assert browser.get('/manage/experiments/status').json()['runs'][0]['gate']['status']=='incomplete'
            target.pop('deny_read')
            target['scores'].pop()
            incomplete=browser.post('/manage/experiments/'+identity+'/readback',json={}).json()
            assert incomplete['gate']['status']=='incomplete' and incomplete['gate']['missing']>=1

            # Real prompt content causes selling regression despite correct reference facts.
            regressed=browser.post('/manage/experiments/start',json={**body,'request_id':'selling','candidate_version':2})
            assert regressed.status_code==202
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                regression=browser.get('/manage/experiments/status').json()['runs'][-1]
                if regression['status']!='running':break
                time.sleep(.05)
            regression=browser.post('/manage/experiments/'+regression['id']+'/readback',json={}).json()
            assert regression['gate']['status']=='failed' and regression['gate']['failed']>0,regression.get('gate')
            assert all(r['checks'].get('must-include',1)==1 for r in regression['arms']['candidate']['rows'])
            # The broad retrieval policy is measured through actual provider usage, not a configured label.
            started=browser.post('/manage/experiments/start',json={**body,'request_id':'retrieval-cost','candidate_policy':'broad-retrieval'})
            assert started.status_code==202
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                broad=browser.get('/manage/experiments/status').json()['runs'][-1]
                if broad['status']!='running':break
                time.sleep(.05)
            broad=browser.post('/manage/experiments/'+broad['id']+'/readback',json={}).json()
            assert broad['gate']['status']=='failed' and broad['gate']['failed']>0,broad.get('gate')
            assert all(all(v==1 for v in r['checks'].values()) for arm in broad['arms'].values() for r in arm['rows'])
            calls=len(target['calls'])
            # Actual MCP lookback and structured confirmation: recent cases survive, older charges fail.
            started=browser.post('/manage/experiments/start',json={**body,'request_id':'dispute-window','cohort':'disputes','candidate_policy':'short-lookback'})
            assert started.status_code==202
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                disputes=browser.get('/manage/experiments/status').json()['runs'][-1]
                if disputes['status']!='running':break
                time.sleep(.05)
            disputes_response=browser.post('/manage/experiments/'+disputes['id']+'/readback',json={})
            assert disputes_response.status_code==200,disputes_response.text
            disputes=disputes_response.json()
            assert disputes['gate']['status']=='failed',disputes.get('gate')
            assert [r['checks']['dispute-correct-transaction'] for r in disputes['arms']['baseline']['rows']]==[1]*10
            assert sum(r['checks']['dispute-correct-transaction'] for r in disputes['arms']['candidate']['rows'])==2
            assert all(r['turns']==2 for r in disputes['arms']['baseline']['rows'])
            assert all(r['verified'] for arm in disputes['arms'].values() for r in arm['rows'])
            calls=len(target['calls'])
            started=browser.post('/manage/experiments/start',json={**body,'request_id':'spanish-language','cohort':'spanish','candidate_version':2})
            assert started.status_code==202
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                spanish=browser.get('/manage/experiments/status').json()['runs'][-1]
                if spanish['status']!='running':break
                time.sleep(.05)
            spanish=browser.post('/manage/experiments/'+spanish['id']+'/readback',json={}).json()
            assert spanish['gate']['status']=='failed',spanish.get('gate')
            assert all(r['checks']['language-match']==1 for r in spanish['arms']['baseline']['rows'])
            assert all(r['checks']['language-match']==0 for r in spanish['arms']['candidate']['rows'])
            calls=len(target['calls'])
            # Current cap denial starts no new provider request and never retries a failed item.
            target['deny_after']=len(target['calls'])
            denied=browser.post('/manage/experiments/start',json={**body,'request_id':'cap-denied'})
            assert denied.status_code==202
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                denied_run=browser.get('/manage/experiments/status').json()['runs'][-1]
                if denied_run['status']!='running':break
                time.sleep(.02)
            assert denied_run['status']=='runner_failed',denied_run
            assert len(target['calls'])==calls
            assert denied_run['gate']['status']=='runner_failed'
            assert browser.post('/manage/experiments/start',json={**body,'request_id':'cap-denied'}).json()['id']==denied.json()['id']
            assert len(target['calls'])==calls
            # The first in-flight provider request may complete; cancellation starts no later calls.
            from threading import Event
            target.pop('deny_after');entered,release=Event(),Event();target['hold']=(entered,release)
            started=browser.post('/manage/experiments/start',json={**body,'request_id':'cancelled'})
            assert started.status_code==202 and entered.wait(5)
            assert browser.post('/manage/experiments/cancel',json={}).status_code==200
            release.set()
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                cancelled=browser.get('/manage/experiments/status').json()['runs'][-1]
                if cancelled['status']!='running':break
                time.sleep(.02)
            assert cancelled['gate']['status']!='passed'
            assert len(target['calls'])==calls+1

            # Edited fixed dataset items are rejected before any paid dispatch.
            target.pop('hold',None)
            item=next(i for i in target['items'].values() if i['metadata']['cohort']=='english')
            item['expectedOutput']='Edited expected answer'
            started=browser.post('/manage/experiments/start',json={**body,'request_id':'edited-cohort'})
            assert started.status_code==202
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                changed=browser.get('/manage/experiments/status').json()['runs'][-1]
                if changed['status']!='running':break
                time.sleep(.02)
            assert changed['status']=='runner_failed' and changed['dispatches']==0
            assert len(target['calls'])==calls+1

        # A restarted application in this container recovers receipts without touching the spool.
        with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as restarted:
            restarted.headers['x-depot-live-proof']='a'*64
            recovered=restarted.get('/manage/experiments/status')
            assert recovered.status_code==200,recovered.text
            assert len(recovered.json()['runs'])==8
            previous=restarted.post('/manage/experiments/start',json=body)
            assert previous.status_code==200 and previous.json()['id']==identity
            assert len(target['calls'])==calls+1
            assert {p.name:p.read_bytes() for p in state.iterdir() if p.is_file()} == before
