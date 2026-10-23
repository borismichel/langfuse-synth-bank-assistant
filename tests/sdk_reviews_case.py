"""Reviewer actions against a real SDK and an external native API fixture."""
import json
import pytest
from sdk_judges_case import presenter
from judge_target import judge_target


@pytest.mark.parametrize('binding',['foreign_instance','foreign_environment','missing_instance'])
def test_foreign_queued_annotation_stays_unresolved_without_modifying_reviews(tmp_path,monkeypatch,binding):
    with judge_target() as (env,target):
        target.update(queues={},queue_items={})
        install_reviews(target);receipt(tmp_path,env,monkeypatch);install_calibration(target,tmp_path)
        with presenter(env,monkeypatch) as browser:
            assert browser.post('/manage/reviews/prepare',json={'request_id':'prep','pending':2}).status_code==200
            foreign=next(row for row in target['observations'] if row['id']=='foreign')
            if binding=='foreign_environment':
                foreign['metadata']['live_instance_id']='test-instance'
                foreign['environment']='other-environment'
            elif binding=='missing_instance':foreign['metadata'].pop('live_instance_id')
            item={**next(iter(target['queue_items'].values())),'id':'manual-foreign',
                'objectId':'foreign','status':'COMPLETED'}
            target['queue_items'][item['id']]=item
            before=json.dumps(target['queue_items'],sort_keys=True)
            stamp=foreign['startTime']
            target['scores']=[dict(id='foreign-review',projectId='bank-project',name='nw-faithfulness-v1',
                source='ANNOTATION',dataType='NUMERIC',value=1,configId='faithfulness-config',queueId='queue-1',
                authorUserId='reviewer-fixture',timestamp=stamp,createdAt=stamp,updatedAt=stamp,
                environment=foreign['environment'],subject={'kind':'observation','id':'foreign','traceId':'trace-foreign'})]
            result=browser.post('/manage/reviews/plan',json={'request_id':'foreign-plan','model':'fixture-judge','include_reviews':True})
            assert result.status_code==200,result.text
            plan=result.json()['calibration']
            assert plan['annotation_rows']==0
            assert plan['unresolved_reviews'][0]['status']=='source_ineligible'
            assert plan['unresolved_reviews'][0]['observation_id']=='foreign'
            assert json.dumps(target['queue_items'],sort_keys=True)==before
            assert not any('/messages' in w[1] or '/scores' in w[1] or w[0] in ('DELETE','PATCH') for w in target['writes'])


def test_prepare_preserves_completed_native_reviews_and_refills_only_pending(tmp_path,monkeypatch):
    with judge_target() as (env,target):
        target.update(queues={},queue_items={})
        install_reviews(target)
        receipt(tmp_path,env,monkeypatch)
        with presenter(env,monkeypatch) as browser:
            browser.headers.pop('x-depot-live-proof')
            assert browser.get('/manage/reviews').status_code==404
            browser.headers['x-depot-live-proof']='a'*64
            page=browser.get('/manage/reviews')
            assert page.status_code==200 and 'Review and calibration' in page.text
            response=browser.post('/manage/reviews/prepare',json={'request_id':'one','pending':2})
            assert response.status_code==200,response.text
            state=response.json()
            assert state['queue']['pending']==2
            assert state['queue']['completed']==0
            assert len(target['queues'])==1 and len(target['queue_items'])==2
            first=next(iter(target['queue_items'].values()));first['status']='COMPLETED'
            before=json.dumps(first,sort_keys=True)
            again=browser.post('/manage/reviews/prepare',json={'request_id':'two','pending':2})
            assert again.status_code==200,again.text
            assert again.json()['queue']['pending']==1
            assert again.json()['queue']['completed']==1
            assert again.json()['queue']['shortfall']==1
            assert json.dumps(first,sort_keys=True)==before
            assert len(target['queue_items'])==2
            assert not any(w[0] in ('DELETE','PATCH') for w in target['writes'])


def receipt(folder,env,monkeypatch):
    from synth.assets import corpus,fingerprint
    monkeypatch.setenv('SYNTH_STATE_DIR',str(folder))
    common=dict(base_url=env['LANGFUSE_BASE_URL'],public_key=env['LANGFUSE_PUBLIC_KEY'],
                project_id='bank-project',schema_version=1,corpus_hash=fingerprint(corpus()))
    (folder/'asset-identities.json').write_text(json.dumps({**common,'score_definitions':{
        'nw-faithfulness-v1':{'id':'faithfulness-config','description':'Faithfulness rubric'}},'prompts':{}}))
    (folder/'asset-receipt.json').write_text(json.dumps({**common,'history_run_id':'fixed-run','datasets':{}}))


def install_reviews(target):
    from datetime import datetime,timezone
    stamp=datetime.now(timezone.utc).isoformat()
    def handle(method,path,query,body):
        if 'annotation-queues' not in path and 'score-configs' not in path:return None
        if path.endswith('/score-configs/faithfulness-config'):
            return 200,dict(id='faithfulness-config',name='nw-faithfulness-v1',dataType='NUMERIC',
                minValue=0,maxValue=1,description='Faithfulness rubric',isArchived=False,
                projectId='bank-project',createdAt=stamp,updatedAt=stamp)
        if method=='GET':
            if path.endswith('/items'):rows=list(target['queue_items'].values())
            else:rows=list(target['queues'].values())
            return 200,dict(data=rows,meta=dict(page=1,limit=100,totalPages=1,totalItems=len(rows)))
        if method=='POST':
            if path.endswith('/items'):
                row={**body,'id':'item-'+body['objectId'],'queueId':'queue-1','createdAt':stamp,'updatedAt':stamp}
                target['queue_items'][row['id']]=row
            else:
                row={**body,'id':'queue-1','createdAt':stamp,'updatedAt':stamp};target['queues']['queue-1']=row
            return 201,row
        raise AssertionError((method,path,body))
    target['extension']=handle


def test_calibration_plan_freezes_constructed_cohort_and_native_reviewer_identity(tmp_path,monkeypatch):
    with judge_target() as (env,target):
        target.update(queues={},queue_items={})
        install_reviews(target);receipt(tmp_path,env,monkeypatch);install_calibration(target,tmp_path)
        with presenter(env,monkeypatch) as browser:
            assert browser.post('/manage/reviews/prepare',json={'request_id':'prep','pending':2}).status_code==200
            stamp=target['observations'][0]['startTime']
            base=dict(projectId='bank-project',name='nw-faithfulness-v1',source='ANNOTATION',dataType='NUMERIC',
                value=1,configId='faithfulness-config',queueId='queue-1',authorUserId='real-reviewer-fixture',
                timestamp=stamp,createdAt=stamp,updatedAt=stamp,environment='production',
                subject={'kind':'observation','id':'english','traceId':'trace-english'})
            target['scores']=[{**base,'id':'review-1'},{**base,'id':'review-1-copy'},
                {**base,'id':'scripted','source':'API','authorUserId':None,'value':0}]
            r=browser.post('/manage/reviews/plan',json={'request_id':'plan-one','model':'fixture-judge','include_reviews':True})
            assert r.status_code==200,r.text
            plan=r.json()['calibration']
            assert plan['constructed_rows']==16 and plan['annotation_rows']==1 and plan['max_model_calls']==17
            assert plan['rows'][-1]['reviewer_ids']==['real-reviewer-fixture']
            assert len(plan['rows'][-1]['score_ids'])==2
            assert plan['rubric_version']==7 and plan['status']=='planned'
            # Conflicting reviewers must stay unresolved, never become a majority ground truth.
            target['scores'].append({**base,'id':'second-reviewer','authorUserId':'other-reviewer-fixture','value':0})
            r=browser.post('/manage/reviews/plan',json={'request_id':'plan-two','model':'fixture-judge','include_reviews':True})
            assert r.status_code==200,r.text
            assert r.json()['calibration']['annotation_rows']==0
            assert r.json()['calibration']['unresolved_reviews'][0]['status']=='reviewer_disagreement'
            assert not any('/messages' in w[1] or '/scores' in w[1] for w in target['writes'])


def install_calibration(target,folder):
    from synth.assets import corpus,fingerprint
    source=corpus();prior=target['extension'];stamp='2026-10-22T10:00:00Z'
    ident=json.loads((folder/'asset-identities.json').read_text())
    ident['prompts']={'faithfulness-v1':dict(name='fixed-faithfulness',version=7,url='http://native/prompt')}
    (folder/'asset-identities.json').write_text(json.dumps(ident))
    rows=[dict(id='cal-'+c['id'],datasetName='fixed-calibration',datasetId='calibration-id',input=c['input'],
        expectedOutput=c['expected_output'],status='ACTIVE',metadata=c['metadata'],
        createdAt=stamp,updatedAt=stamp,version=stamp,mediaReferences=[]) for c in source['cohorts']['calibration']]
    saved=json.loads((folder/'asset-receipt.json').read_text());saved['datasets']['calibration']=dict(
        id='calibration-id',name='fixed-calibration',snapshot_at=stamp,url='http://native/dataset',
        cohort_hash=fingerprint(source['cohorts']['calibration']),item_ids=[r['id'] for r in rows])
    (folder/'asset-receipt.json').write_text(json.dumps(saved))
    target['dataset_rows']=rows
    def extension(method,path,query,body):
        if method=='GET' and '/prompts/' in path:return 200,dict(name='fixed-faithfulness',version=7,
            prompt=source['prompts']['faithfulness-v1'],type='text',config={},tags=['northwind-depot'],labels=[])
        if method=='GET' and path.endswith('/dataset-items'):
            return 200,dict(data=target['dataset_rows'],meta=dict(page=1,limit=100,totalPages=1,totalItems=16))
        return prior(method,path,query,body)
    target['extension']=extension


def test_calibration_requires_cost_ack_and_fails_closed_without_spending_contract(tmp_path,monkeypatch):
    import time
    with judge_target() as (env,target):
        target.update(queues={},queue_items={});install_reviews(target);receipt(tmp_path,env,monkeypatch)
        install_calibration(target,tmp_path)
        with presenter(env,monkeypatch) as browser:
            p=browser.post('/manage/reviews/plan',json={'request_id':'bounded','model':'fixture-judge','include_reviews':False}).json()['calibration']
            payload={'request_id':'bounded','snapshot_hash':p['snapshot_hash'],'cost_acknowledged':False}
            assert browser.post('/manage/reviews/start',json=payload).status_code==400
            payload['cost_acknowledged']=True
            started=browser.post('/manage/reviews/start',json=payload)
            assert started.status_code==202,started.text
            for _ in range(100):
                state=browser.get('/manage/reviews/status').json()['calibration']
                if state['status']!='running':break
                time.sleep(.02)
            assert state['status']=='blocked'
            assert state['completed']==0 and state['model_calls']==0
            assert 'spending' in state['error'].lower()
            assert browser.post('/manage/reviews/start',json=payload).status_code==200
            assert not target.get('model_calls')


def spending_fixture(env,target,monkeypatch):
    import socket
    from urllib.parse import urlparse
    instance='bde5ea54-d6dc-4e43-95a4-3696886c9620'
    env.update(PORTAL_LIVE_INSTANCE_ID=instance,DEPOT_OPERATION_TOKEN='b'*64,
        DEPOT_OPERATION_CHECK_URL=f'http://caddy:8081/api/v1/live-assets/{instance}/operation-permit',
        ANTHROPIC_BASE_URL=env['LANGFUSE_BASE_URL'])
    original=socket.getaddrinfo;port=urlparse(env['LANGFUSE_BASE_URL']).port
    def resolve(host,p,*args,**kw):
        return original('127.0.0.1',port,*args,**kw) if host=='caddy' and p==8081 else original(host,p,*args,**kw)
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    prior=target['extension'];target.update(permits=[],model_calls=[],experiment_items=[],experiments=[])
    def extension(method,path,query,body):
        if path.endswith('/operation-permit'):
            target['permits'].append(body)
            if target.get('deny_after') is not None and len(target['model_calls'])>=target['deny_after']:
                return 409,{'reason':'cap_exhausted'}
            return 200,dict(allowed=True,provider='anthropic',source='shared',checked_at='2026-10-23T12:00:00+00:00',accounting='hourly-estimate',scope='shared-company')
        if path.endswith('/messages'):
            target['model_calls'].append(body)
            raw=target.get('responses',['{"score":1,"reason":"Fixture"}'])
            text=raw[(len(target['model_calls'])-1)%len(raw)]
            return 200,dict(id='message-fixture',type='message',role='assistant',model=body['model'],
                content=[{'type':'text','text':text}],stop_reason='end_turn',stop_sequence=None,usage={'input_tokens':30,'output_tokens':12})
        if method=='POST' and path.endswith('/scores') and isinstance(body,list) and any(x.get('name','').startswith('judge-') for x in body):
            from datetime import datetime,timezone
            stamp=datetime.now(timezone.utc).isoformat()
            for score in body:
                subject=({'kind':'experiment','id':score['datasetRunId']} if score.get('datasetRunId') else
                    {'kind':'observation','id':score['observationId'],'traceId':score['traceId']})
                target.setdefault('calibration_scores',[]).append({**score,'source':'API','projectId':'bank-project',
                    'subject':subject,'environment':score.get('environment','default'),'createdAt':stamp,'updatedAt':stamp})
            return 202,{'message':'Accepted'}
        if method=='GET' and path.endswith('/experiments'):
            return 200,dict(data=target['experiments'],meta={'cursor':None})
        if method=='GET' and path.endswith('/experiment-items'):
            return 200,dict(data=target['experiment_items'],meta={'cursor':None})
        return prior(method,path,query,body)
    target['extension']=extension


def test_calibration_measures_actual_results_with_strict_invalids_and_one_dispatch_per_permit(tmp_path,monkeypatch):
    import time
    with judge_target() as (env,target):
        target.update(queues={},queue_items={});install_reviews(target);receipt(tmp_path,env,monkeypatch)
        install_calibration(target,tmp_path);spending_fixture(env,target,monkeypatch)
        target['responses']=['{"score":1,"reason":"Fixture"}','{"score":0.7}','not JSON']
        with presenter(env,monkeypatch) as browser:
            response=browser.post('/manage/reviews/plan',json={'request_id':'measured','model':'fixture-judge','include_reviews':False})
            assert response.status_code==200,response.text
            p=response.json()['calibration'];body={'request_id':'measured','snapshot_hash':p['snapshot_hash'],'cost_acknowledged':True}
            assert browser.post('/manage/reviews/start',json=body).status_code==202
            for _ in range(400):
                state=browser.get('/manage/reviews/status').json()['calibration']
                if state['status']!='running':break
                time.sleep(.02)
            assert state['status']=='completed',state
            assert state['completed']==16 and state['model_calls']==16
            assert len(target['model_calls'])==16 and len(target['permits'])==17
            assert state['metrics']['constructed']=={'total':16,'valid':6,'invalid_or_unresolved':10,'matches':2,'accuracy':2/6}
            assert state['metrics']['native_annotation']['accuracy'] is None
            assert state['native_status']=='pending_readback'
            generations=[span for span in target['exports'] if span.name=='faithfulness-judge']
            assert len(generations)==16
            assert len({span.trace_id for span in generations})==16
            assert browser.post('/manage/reviews/readback').json()['calibration']['native_status']=='pending_readback'
            publish_experiment(target)
            native=browser.post('/manage/reviews/readback')
            assert native.status_code==200,native.text
            assert native.json()['calibration']['native_status']=='confirmed',native.text
            target['experiment_items'][0]['output']['actual']=0.5
            assert browser.post('/manage/reviews/readback').json()['calibration']['native_status']=='conflict'
            assert browser.post('/manage/reviews/start',json=body).status_code==200
            assert len(target['model_calls'])==16
            assert all('expected_output' not in str(c) and 'calibration_case_id' not in str(c) for c in target['model_calls'])


def publish_experiment(target):
    from datetime import datetime,timezone
    rows=[]
    for span in target['exports']:
        if span.name!='experiment-item-task':continue
        a={item.key:item.value.string_value for item in span.attributes}
        start=datetime.fromtimestamp(span.start_time_unix_nano/1e9,timezone.utc).isoformat()
        end=datetime.fromtimestamp(span.end_time_unix_nano/1e9,timezone.utc).isoformat()
        rows.append(dict(id=span.span_id.hex(),traceId=span.trace_id.hex(),startTime=start,endTime=end,level='DEFAULT',
            environment='sdk-experiment',experimentId=a['langfuse.experiment.id'],experimentName=a['langfuse.experiment.name'],
            experimentItemId=a['langfuse.experiment.item.id'],
            input=json.loads(a['langfuse.observation.input']),output=json.loads(a['langfuse.observation.output']),
            expectedOutput=json.loads(a['langfuse.experiment.item.expected_output']),
            experimentItemMetadata=json.loads(a['langfuse.experiment.item.metadata']),
            experimentMetadata=json.loads(a['langfuse.experiment.metadata']),scores=[]))
    target['experiment_items']=rows
    target['experiments']=[dict(id=rows[0]['experimentId'],name=rows[0]['experimentName'],startTime=rows[0]['startTime'],
        endTime=rows[-1]['endTime'],itemCount=len(rows),metadata=rows[0]['experimentMetadata'],
        scores=[s for s in target.get('calibration_scores',[]) if s['subject']['kind']=='experiment'])]


def test_cap_stop_blocks_new_calls_and_same_action_survives_restart(tmp_path,monkeypatch):
    import time
    with judge_target() as (env,target):
        target.update(queues={},queue_items={});install_reviews(target);receipt(tmp_path,env,monkeypatch)
        install_calibration(target,tmp_path);spending_fixture(env,target,monkeypatch);target['deny_after']=2
        with presenter(env,monkeypatch) as browser:
            planned=browser.post('/manage/reviews/plan',json={'request_id':'cap-stop','model':'fixture-judge','include_reviews':False})
            p=planned.json()['calibration'];body=dict(request_id='cap-stop',snapshot_hash=p['snapshot_hash'],cost_acknowledged=True)
            assert browser.post('/manage/reviews/start',json=body).status_code==202
            for _ in range(200):
                state=browser.get('/manage/reviews/status').json()['calibration']
                if state['status']!='running':break
                time.sleep(.02)
            assert state['status']=='blocked',state
            assert state['halt_reason']=='cap_exhausted'
            assert len(target['model_calls'])==2
        with presenter(env,monkeypatch) as browser:
            recovered=browser.get('/manage/reviews/status').json()['calibration']
            assert recovered['request_id']=='cap-stop' and recovered['status']=='blocked'
            assert browser.post('/manage/reviews/start',json=body).status_code==200
            assert len(target['model_calls'])==2


def test_cancel_duplicate_start_and_completed_reviews_survive_new_prepare(tmp_path,monkeypatch):
    import time
    from threading import Event
    entered,release=Event(),Event()
    with judge_target() as (env,target):
        target.update(queues={},queue_items={});install_reviews(target);receipt(tmp_path,env,monkeypatch)
        install_calibration(target,tmp_path);spending_fixture(env,target,monkeypatch)
        prior=target['extension']
        def extension(method,path,query,body):
            result=prior(method,path,query,body)
            if path.endswith('/messages'):
                entered.set();assert release.wait(5)
            return result
        target['extension']=extension
        with presenter(env,monkeypatch) as browser:
            planned=browser.post('/manage/reviews/plan',json={'request_id':'cancel-action','model':'fixture-judge','include_reviews':False})
            p=planned.json()['calibration'];body=dict(request_id='cancel-action',snapshot_hash=p['snapshot_hash'],cost_acknowledged=True)
            assert browser.post('/manage/reviews/start',json=body).status_code==202
            try:
                assert entered.wait(5)
                assert browser.post('/manage/reviews/start',json=body).status_code==200
                assert browser.post('/manage/reviews/plan',json={'request_id':'other','model':'fixture-judge'}).status_code==409
                assert browser.post('/manage/reviews/cancel').status_code==200
            finally:release.set()
            for _ in range(200):
                state=browser.get('/manage/reviews/status').json()['calibration']
                if state['status']!='running':break
                time.sleep(.02)
            assert state['status']=='cancelled' and state['model_calls']==1
            assert len(target['model_calls'])==1
            assert state['metrics']['constructed']['valid']==1
            assert state['metrics']['constructed']['invalid_or_unresolved']==15


def test_missing_reviewer_identity_and_invalid_labels_stay_unresolved(tmp_path,monkeypatch):
    with judge_target() as (env,target):
        target.update(queues={},queue_items={});install_reviews(target);receipt(tmp_path,env,monkeypatch);install_calibration(target,tmp_path)
        with presenter(env,monkeypatch) as browser:
            browser.post('/manage/reviews/prepare',json={'request_id':'prep','pending':2})
            stamp=target['observations'][0]['startTime']
            target['scores']=[dict(id='unattributed',projectId='bank-project',name='nw-faithfulness-v1',source='ANNOTATION',
                dataType='NUMERIC',value=1,configId='faithfulness-config',queueId='queue-1',authorUserId=None,
                timestamp=stamp,createdAt=stamp,updatedAt=stamp,environment='production',
                subject={'kind':'observation','id':'english','traceId':'trace-english'})]
            response=browser.post('/manage/reviews/plan',json={'request_id':'missing-author','model':'fixture-judge','include_reviews':True})
            assert response.status_code==200,response.text
            p=response.json()['calibration']
            assert p['annotation_rows']==0 and p['unresolved_reviews'][0]['status']=='reviewer_identity_unavailable'
            target['scores'][0].update(authorUserId='reviewer-fixture',value=.7)
            p=browser.post('/manage/reviews/plan',json={'request_id':'invalid-value','model':'fixture-judge','include_reviews':True}).json()['calibration']
            assert p['annotation_rows']==0 and p['unresolved_reviews'][0]['status']=='invalid_annotation'
            target['dataset_rows'][0]['expectedOutput']=0.5
            assert browser.post('/manage/reviews/plan',json={'request_id':'edited-cohort','model':'fixture-judge','include_reviews':False}).status_code==502


def test_action_identity_conflict_cannot_replace_the_reviewed_plan(tmp_path,monkeypatch):
    with judge_target() as (env,target):
        target.update(queues={},queue_items={});install_reviews(target);receipt(tmp_path,env,monkeypatch);install_calibration(target,tmp_path)
        with presenter(env,monkeypatch) as browser:
            first=browser.post('/manage/reviews/plan',json={'request_id':'immutable','model':'first-model','include_reviews':False}).json()['calibration']
            conflict=browser.post('/manage/reviews/plan',json={'request_id':'immutable','model':'other-model','include_reviews':False})
            assert conflict.status_code==502
            current=browser.get('/manage/reviews/status').json()['calibration']
            assert current['model']=='first-model' and current['experiment_id']==first['experiment_id']


def test_calibration_preserves_native_annotation_and_generation_linkage(tmp_path,monkeypatch):
    import time
    from northwind import config
    from sdk_identifiers import numeric_span_ids
    numeric_span_ids(monkeypatch)
    source_id='4111111111111111'  # Public synthetic value, valid as an observation ID.
    with judge_target() as (env,target):
        target.update(queues={},queue_items={});install_reviews(target);receipt(tmp_path,env,monkeypatch)
        install_calibration(target,tmp_path);spending_fixture(env,target,monkeypatch)
        source=target['observations'][0]
        source.update(id=source_id,traceId='a'*32)
        source['metadata']['live_instance_id']=env['PORTAL_LIVE_INSTANCE_ID']
        with presenter(env,monkeypatch,langfuse_factory=config.langfuse_factory) as browser:
            assert browser.post('/manage/reviews/prepare',json={'request_id':'prep-numeric','pending':1}).status_code==200
            stamp=source['startTime']
            target['scores']=[dict(id='native-review-fixture',projectId='bank-project',name='nw-faithfulness-v1',
                source='ANNOTATION',dataType='NUMERIC',value=1,configId='faithfulness-config',queueId='queue-1',
                authorUserId='native-reviewer-fixture',timestamp=stamp,createdAt=stamp,updatedAt=stamp,
                environment='production',subject={'kind':'observation','id':source_id,'traceId':'a'*32})]
            planned=browser.post('/manage/reviews/plan',json={'request_id':'numeric-links','model':'fixture-judge','include_reviews':True})
            assert planned.status_code==200,planned.text
            plan=planned.json()['calibration']
            assert plan['annotation_rows']==1 and plan['constructed_rows']==16
            body={'request_id':'numeric-links','snapshot_hash':plan['snapshot_hash'],'cost_acknowledged':True}
            assert browser.post('/manage/reviews/start',json=body).status_code==202
            for _ in range(400):
                state=browser.get('/manage/reviews/status').json()['calibration']
                if state['status']!='running':break
                time.sleep(.02)
            assert state['status']=='completed',state
            assert state['completed']==state['model_calls']==len(target['model_calls'])==17
            assert len(target['permits'])==18
            publish_experiment(target)
            native=browser.post('/manage/reviews/readback')
            assert native.status_code==200,native.text
            assert native.json()['calibration']['native_status']=='confirmed',native.json()['calibration']['native_status']
            annotation=next(row for row in target['experiment_items'] if row['output']['provenance']=='native_annotation')
            assert annotation['output']['source_observation_id']==source_id
            assert annotation['experimentItemMetadata']['source_observation_id']==source_id
            assert annotation['experimentItemMetadata']['reviewer_ids']==['native-reviewer-fixture']
            assert annotation['experimentItemMetadata']['score_ids']==['native-review-fixture']
            for row,outcome in zip(target['experiment_items'],state['outcomes']):
                assert row['output']['generation_id']==outcome['generation_id']
                assert row['output']['generation_trace_id']==outcome['generation_trace_id']
