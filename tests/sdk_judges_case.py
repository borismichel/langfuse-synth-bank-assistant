"""Managed judging through the delivered protected presenter surface."""
import json,time
from judge_target import judge_target


def test_presenter_explicitly_activates_five_owned_judges_and_can_pause(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app,REQUIRES_SECRETS
    with judge_target() as (env,target):
        for k,v in env.items():monkeypatch.setenv(k,v)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path))
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS)
        app=guarded_app(create_app(adapter),health_path='/api/health')
        with TestClient(app) as browser:
            assert browser.get('/manage/judges').status_code==404
            browser.headers['x-depot-live-proof']='a'*64
            page=browser.get('/manage/judges')
            assert page.status_code==200 and 'Activate policy judges' in page.text
            assert target['writes']==[]
            body=dict(request_id='activate-first',model='fixture-judge-model',sampling=1,cost_acknowledged=True)
            start=browser.post('/manage/judges/activate',json=body)
            assert start.status_code==202,start.text
            for _ in range(100):
                status=browser.get('/manage/judges/status').json()
                if status.get('operation',{}).get('status')!='running':break
                time.sleep(.02)
            assert status['operation']['status']=='succeeded',json.dumps(status,indent=2)
            assert status['enabled'] is True
            assert {r['policy'] for r in status['evaluators']}=={'P1','P2','P3','P4','P5'}
            assert len(target['evaluators'])==5 and len(target['rules'])==1
            rule=next(iter(target['rules'].values()))
            assert len(rule['evaluatorAssignments'])==5 and rule['sampling']==1
            assert len(status['evidence']['eligible'])==2
            assert status['evidence']['negative']['blocked']==1
            assert status['evidence']['negative']['history']==1
            assert status['evidence']['eligible'][0]['generation']=='USD35'
            assert all(r['version']==1 and '/evals/' in r['url'] for r in status['evaluators'])
            writes=len(target['writes'])
            assert browser.post('/manage/judges/activate',json=body).status_code in (200,202)
            assert len(target['writes'])==writes
            paused=browser.post('/manage/judges/pause',json={'request_id':'pause-first'})
            assert paused.status_code==200,paused.text
            assert paused.json()['enabled'] is False
            assert len(target['evaluators'])==5

from contextlib import contextmanager

@contextmanager
def presenter(env,monkeypatch):
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app
    for k,v in env.items():monkeypatch.setenv(k,v)
    adapter=CompanionAdapter(load_config('config/demo.yaml'))
    with TestClient(guarded_app(create_app(adapter),health_path='/api/health')) as browser:
        browser.headers['x-depot-live-proof']='a'*64
        yield browser


def activate(browser,request_id='activate'):
    response=browser.post('/manage/judges/activate',json=dict(request_id=request_id,
        model='fixture-judge-model',sampling=1,cost_acknowledged=True))
    assert response.status_code in (200,202),response.text
    for _ in range(150):
        status=browser.get('/manage/judges/status').json()
        if status.get('operation',{}).get('status')!='running':return status
        time.sleep(.02)
    raise AssertionError('Judge operation did not finish')


def test_readback_recovers_owned_rules_and_accepts_only_exact_managed_subjects(monkeypatch):
    from datetime import datetime,timezone
    with judge_target() as (env,target):
        with presenter(env,monkeypatch) as browser:
            status=activate(browser)
            assert status['operation']['status']=='succeeded',status
        stamp=datetime.now(timezone.utc).isoformat()
        for pid,row in enumerate(target['evaluators'].values(),1):
            target['scores'].append(dict(id=f'score-{pid}',projectId='bank-project',name=row['name'],
                source='EVAL',dataType='CATEGORICAL',value='pass',environment='production',
                timestamp=stamp,createdAt=stamp,updatedAt=stamp,comment='Approved policy evidence.',
                subject={'kind':'observation','id':'english','traceId':'trace-english'},
                metadata={'evaluator_id':row['id'],'evaluator_version_id':row['versionId'],
                    'evaluation_rule_id':'rule-1','target_trace_id':'trace-english','target_observation_id':'english'}))
        # A fixture/API score, another observation and a later evaluator revision cannot fill Spanish coverage.
        for i,change in enumerate(({'source':'API'}, {'subject':{'kind':'observation','id':'child','traceId':'trace-spanish'}},
                {'metadata':{'evaluator_id':'eval-1','evaluator_version_id':'other','evaluation_rule_id':'rule-1'}})):
            target['scores'].append({**target['scores'][0],'id':f'foreign-{i}',
                'subject':{'kind':'observation','id':'spanish','traceId':'trace-spanish'},**change})
        writes=len(target['writes'])
        with presenter(env,monkeypatch) as browser:
            response=browser.get('/manage/judges/status?refresh=true')
            assert response.status_code==200,response.text
            status=response.json()
            assert status['enabled'] is True
            assert len(target['writes'])==writes
            english=next(r for r in status['results'] if r['observation_id']=='english')
            spanish=next(r for r in status['results'] if r['observation_id']=='spanish')
            assert [r['value'] for r in english['policies']]==['pass']*5
            assert all(r['state']=='pending' for r in spanish['policies'])
            assert {k:status['coverage'][k] for k in ('eligible','fully_scored','scored_policy_results','possible_policy_results')}=={'eligible':2,'fully_scored':1,'scored_policy_results':5,'possible_policy_results':10}
            assert browser.post('/manage/judges/pause',json={'request_id':'restart-pause'}).json()['enabled'] is False


def test_restart_does_not_activate_a_second_configuration_and_native_pause_is_visible(monkeypatch):
    with judge_target() as (env,target):
        with presenter(env,monkeypatch) as browser:
            assert activate(browser)['operation']['status']=='succeeded'
        writes=len(target['writes'])
        with presenter(env,monkeypatch) as browser:
            response=browser.post('/manage/judges/activate',json=dict(request_id='changed',
                model='fixture-judge-model',sampling=.5,cost_acknowledged=True))
            assert response.status_code==202
            for _ in range(100):
                status=browser.get('/manage/judges/status').json()
                if status['operation']['status']!='running':break
                time.sleep(.02)
            assert status['operation']['status']=='failed'
            assert 'different judge configuration' in status['operation']['error']
            assert len(target['writes'])==writes
            target['evaluators']['eval-1'].update(status='paused',pausedReason='AUTH_ERROR',pausedMessage='Provider credential rejected')
            status=browser.get('/manage/judges/status?refresh=true').json()
            assert status['enabled'] is True  # Remote rule and paused evaluator are separate facts.
            assert status['evaluators'][0]['status']=='paused'
            assert status['evaluators'][0]['paused_reason']=='AUTH_ERROR'
            assert status['results'][0]['policies'][0]['state']=='paused'
            target['evaluators']['eval-1']['version']=2
            status=browser.get('/manage/judges/status?refresh=true').json()
            assert 'latest version changed' in status['readback_error']
            assert status['enabled'] is None  # Do not invent disabled state after failed readback.
            assert len(target['writes'])==writes

import pytest

@pytest.mark.parametrize('problem', ['missing-key','permission','no-eligible','missing-context'])
def test_activation_prerequisites_fail_without_creating_models_or_rules(monkeypatch,problem):
    with judge_target() as (env,target):
        if problem=='missing-key':env['ANTHROPIC_API_KEY']=''
        if problem=='permission':target['fail']='/api/public/v2/evaluation-rules'
        if problem=='no-eligible':
            for row in target['observations']:row['metadata']['judge_eligible']='false'
        if problem=='missing-context':target['observations'][0]['metadata']['context']=''
        with presenter(env,monkeypatch) as browser:
            response=browser.post('/manage/judges/activate',json=dict(request_id='unacknowledged',
                model='fixture-judge-model',sampling=1,cost_acknowledged=False))
            assert response.status_code==400
            status=activate(browser)
            assert status['operation']['status']=='failed',status
            assert status['operation']['error']
            assert not target['writes']


def test_uncertain_creation_is_recovered_without_duplicates_or_implicit_activation(monkeypatch):
    with judge_target() as (env,target):
        target['fail_create_once']=3
        with presenter(env,monkeypatch) as browser:
            status=activate(browser)
            assert status['operation']['status']=='failed'
            assert len(target['evaluators'])==3 and target['rules']=={}
            writes=len(target['writes'])
            status=browser.get('/manage/judges/status?refresh=true').json()
            assert len(target['writes'])==writes
            status=activate(browser,'explicit-retry')
            assert status['operation']['status']=='succeeded',json.dumps(status)
            assert len(target['evaluators'])==5 and len(target['rules'])==1
            assert len([w for w in target['writes'] if w[0]=='POST' and w[1].endswith('/evaluators')])==5
            # An unchanged replay cannot schedule a new activation.
            writes=len(target['writes']);activate(browser,'explicit-retry')
            assert len(target['writes'])==writes


def test_uncertain_activation_reports_unknown_until_native_readback(monkeypatch):
    with judge_target() as (env,target):
        target['fail_enable_once']=True
        with presenter(env,monkeypatch) as browser:
            status=activate(browser)
            assert status['operation']['status']=='failed'
            assert status['enabled'] is None
            writes=len(target['writes'])
            status=browser.get('/manage/judges/status?refresh=true').json()
            assert status['enabled'] is True
            assert len(target['writes'])==writes


def test_stopping_companion_during_preparation_never_enables_a_paid_rule(monkeypatch):
    from threading import Event,Timer
    with judge_target() as (env,target):
        entered,release=Event(),Event()
        target['hold_create_once']=(entered,release)
        with presenter(env,monkeypatch) as browser:
            response=browser.post('/manage/judges/activate',json=dict(request_id='interrupted',
                model='fixture-judge-model',sampling=1,cost_acknowledged=True))
            assert response.status_code==202
            assert entered.wait(5)
            Timer(.1,release.set).start()
        assert not any(rule['enabled'] for rule in target['rules'].values())
        assert len(target['evaluators'])==1


def test_deleted_model_connection_is_visible_without_recreating_it(monkeypatch):
    with judge_target() as (env,target):
        with presenter(env,monkeypatch) as browser:
            assert activate(browser)['operation']['status']=='succeeded'
            target['connections'].clear()
            writes=len(target['writes'])
            status=browser.get('/manage/judges/status?refresh=true').json()
            assert 'model connection is missing' in status['readback_error']
            assert len(target['writes'])==writes


def test_native_rule_deletion_is_not_reported_as_enabled_from_cache(monkeypatch):
    with judge_target() as (env,target):
        with presenter(env,monkeypatch) as browser:
            assert activate(browser)['operation']['status']=='succeeded'
            target['rules'].clear()
            writes=len(target['writes'])
            status=browser.get('/manage/judges/status?refresh=true').json()
            assert status['enabled'] is False
            assert status['rule_id'] is None
            assert len(target['writes'])==writes
