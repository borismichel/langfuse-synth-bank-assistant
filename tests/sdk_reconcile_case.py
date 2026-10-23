"""Delivered reduction and reconciliation against the actual SDK HTTP boundary."""
from datetime import datetime, timezone, timedelta
from judge_target import judge_target
from sdk_judges_case import presenter, activate


def verdicts(target, labels, *, observation='english', revision=0):
    stamp=(datetime.now(timezone.utc)+timedelta(seconds=revision)).isoformat()
    rows=[]
    for pid,value in labels.items():
        evaluator=list(target['evaluators'].values())[int(pid[1])-1]
        rows.append(dict(id=f'{observation}-{pid}-{revision}',projectId='bank-project',name=evaluator['name'],
            source='EVAL',dataType='CATEGORICAL',value=value,environment='production',
            timestamp=stamp,createdAt=stamp,updatedAt=stamp,comment='Fixture policy evidence.',
            subject={'kind':'observation','id':observation,'traceId':'trace-'+observation},
            metadata={'evaluator_id':evaluator['id'],'evaluator_version_id':evaluator['versionId'],
                'evaluation_rule_id':'rule-1','target_trace_id':'trace-'+observation,'target_observation_id':observation}))
    target['scores'].extend(rows)
    return rows


def read(browser):
    response=browser.get('/manage/judges/status?refresh=true')
    assert response.status_code==200,response.text
    status=response.json()
    assert status['readback_error'] is None,status
    return status


def answer(status,observation='english'):
    return next(r for r in status['results'] if r['observation_id']==observation)


def test_delivered_headline_retains_missing_unknown_failure_and_denominators(monkeypatch):
    with judge_target() as (env,target),presenter(env,monkeypatch) as browser:
        assert activate(browser)['operation']['status']=='succeeded'
        status=read(browser)
        assert answer(status)['headline']=={'status':'unscored','score':None,'coverage':0.0}
        verdicts(target,{'P1':'pass','P3':'minor','P4':'fail','P5':'invalid'})
        status=read(browser)
        assert answer(status)['headline']=={'status':'violation','score':0.0,'coverage':0.6}
        assert status['coverage']['unscored']==1
        assert status['coverage']['blocked']==1
        assert status['coverage']['pending']==2
        assert status['coverage']['error']==1
        assert status['coverage']['scored']==1
        assert answer(status,'spanish')['headline']['score'] is None
        verdicts(target,{'P2':'pass','P4':'pass','P5':'pass'},revision=2)
        assert answer(read(browser))['headline']=={'status':'minor-issue','score':0.5,'coverage':1.0}


def reconcile(browser):
    response=browser.post('/manage/judges/reconcile',json={'request_id':'read-model-free'})
    assert response.status_code==200,response.text
    return response.json()


def test_model_free_reconciliation_saves_exact_subject_and_retracts_only_owned_numeric(monkeypatch):
    with judge_target() as (env,target),presenter(env,monkeypatch) as browser:
        assert activate(browser)['operation']['status']=='succeeded'
        verdicts(target,{f'P{i}':'pass' for i in range(1,6)})
        status=reconcile(browser)
        assert status['reconciliation']['status']=='confirmed',status
        owned=[r for r in target['scores'] if r['source']=='API']
        headline=next(r for r in owned if r['name']=='banking-compliance')
        assert headline['value']==1 and headline['subject']=={'kind':'observation','id':'english','traceId':'trace-english'}
        assert headline['metadata']['live_run_id']=='run-english'
        assert not any(r['name']=='banking-compliance' and r['subject']['id']=='spanish' for r in owned)
        target['scores'].append({**headline,'id':'human-rating','source':'ANNOTATION'})
        target['scores'].append({**headline,'id':'legacy-rating','metadata':{'origin':'legacy'}})
        writes=len(target['writes']);reconcile(browser)
        assert len(target['writes'])==writes  # Native readback makes unchanged replay a no-op.
        verdicts(target,{'P4':'unknown'},revision=2)
        status=reconcile(browser)
        assert answer(status)['headline']['score'] is None
        assert not any(r['id']==headline['id'] for r in target['scores'])
        assert {'human-rating','legacy-rating'}<={r['id'] for r in target['scores']}
        assert len(target['evaluators'])==5  # Reconciliation never requests a model/evaluator.


def test_paged_reordered_duplicates_and_late_results_keep_native_identity(monkeypatch):
    with judge_target() as (env,target),presenter(env,monkeypatch) as browser:
        assert activate(browser)['operation']['status']=='succeeded'
        target['page_size']=3
        first=verdicts(target,{f'P{i}':'pass' for i in range(1,6)})
        target['scores'].extend(first)  # Repeated page rows must not manufacture ambiguity.
        target['scores'].reverse()
        assert answer(reconcile(browser))['headline']['score']==1
        ids={r['id'] for r in target['scores'] if r['source']=='API'}
        verdicts(target,{'P2':'fail'},revision=3)
        target['scores'].reverse()
        assert answer(reconcile(browser))['headline']['score']==0
        assert {r['id'] for r in target['scores'] if r['source']=='API'}==ids
        assert any(q.get('cursor') for path,q in target['reads'] if path.endswith('/scores'))
        # Same newest timestamp, contradictory values: fail still dominates uncertainty.
        latest=next(r for r in target['scores'] if r['id']=='english-P2-3')
        target['scores'].append({**latest,'id':'tied-pass','value':'pass'})
        status=reconcile(browser)
        assert answer(status)['headline']['score']==0
        assert status['coverage']['error']==1


def test_ownership_collision_and_incomplete_cursor_scan_never_mutate_scores(monkeypatch):
    with judge_target() as (env,target),presenter(env,monkeypatch) as browser:
        assert activate(browser)['operation']['status']=='succeeded'
        verdicts(target,{f'P{i}':'pass' for i in range(1,6)})
        assert reconcile(browser)['reconciliation']['status']=='confirmed'
        owned=next(r for r in target['scores'] if r['name']=='banking-compliance')
        owned['source']='ANNOTATION'
        verdicts(target,{'P4':'unknown'},revision=2)
        writes=len(target['writes'])
        status=reconcile(browser)
        assert status['reconciliation']['status']=='error'
        assert 'ownership conflict' in status['reconciliation']['error']
        assert len(target['writes'])==writes
        owned['source']='API';target['repeated_cursor']=True
        status=reconcile(browser)
        assert status['reconciliation']['status']=='error'
        assert 'cursor' in status['reconciliation']['error']
        assert len(target['writes'])==writes


def test_partial_acceptance_stops_automatic_writes_until_explicit_retry(monkeypatch):
    with judge_target() as (env,target),presenter(env,monkeypatch) as browser:
        assert activate(browser)['operation']['status']=='succeeded'
        verdicts(target,{f'P{i}':'pass' for i in range(1,6)})
        target['partial_batch']=True
        status=reconcile(browser)
        assert status['reconciliation']['status']=='error'
        assert 'partially rejected' in status['reconciliation']['error']
        target['partial_batch']=False
        status=reconcile(browser)
        assert status['reconciliation']['status']=='confirmed'
        assert len([r for r in target['scores'] if r['source']=='API'])==5


def test_reconciliation_catches_up_on_restart_and_stops_with_companion(monkeypatch):
    import time
    with judge_target() as (env,target):
        with presenter(env,monkeypatch) as browser:
            assert activate(browser)['operation']['status']=='succeeded'
        verdicts(target,{f'P{i}':'pass' for i in range(1,6)})
        with presenter(env,monkeypatch) as browser:
            status=browser.get('/manage/judges/status').json()
            assert status['reconciliation']['lifecycle']=='running'
            deadline=time.monotonic()+36
            while time.monotonic()<deadline:
                status=browser.get('/manage/judges/status').json()
                if status['reconciliation']['status']=='confirmed':break
                time.sleep(.2)
            assert status['reconciliation']['status']=='confirmed',status
            assert answer(status)['headline']['score']==1
        read_count=len(target['reads']);write_count=len(target['writes'])
        time.sleep(.15)
        assert len(target['reads'])==read_count and len(target['writes'])==write_count
        assert target['rules']['rule-1']['enabled'] is True


def test_accepted_is_not_confirmed_until_native_readback_and_restart_retains_hold(monkeypatch):
    with judge_target() as (env,target):
        with presenter(env,monkeypatch) as browser:
            assert activate(browser)['operation']['status']=='succeeded'
            verdicts(target,{f'P{i}':'pass' for i in range(1,6)})
            target['delay_scores']=True
            status=reconcile(browser)
            assert status['reconciliation']['status']=='pending',status
            target['scores'].extend(target.pop('delayed',[]));target['delay_scores']=False
            assert reconcile(browser)['reconciliation']['status']=='confirmed'
            verdicts(target,{'P1':'fail'},revision=1)
            target['partial_batch']=True
            assert reconcile(browser)['reconciliation']['status']=='error'
        target['partial_batch']=False
        with presenter(env,monkeypatch) as browser:
            assert browser.get('/manage/judges/status').json()['reconciliation']['status']=='action_required'
            assert reconcile(browser)['reconciliation']['status']=='confirmed'


def test_only_one_companion_owns_reconciliation_for_a_deployment(monkeypatch):
    with judge_target() as (env,target),presenter(env,monkeypatch) as first:
        assert first.get('/manage/judges/status').json()['reconciliation']['lifecycle']=='running'
        with presenter(env,monkeypatch) as second:
            state=second.get('/manage/judges/status').json()['reconciliation']
            assert state['status']=='another_owner'
            assert second.post('/manage/judges/reconcile',json={'request_id':'duplicate-owner'}).status_code==409
        assert target['writes']==[]
