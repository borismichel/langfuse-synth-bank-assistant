"""Matched judging of frozen conduct answers; no application replay or human labels."""
from pydantic import BaseModel,Field

class ConductRequest(BaseModel):
    request_id:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    judge_model:str=Field(min_length=1,max_length=120,pattern=r'^[A-Za-z0-9._:/-]+$')
    include_legacy:bool=False
    cost_acknowledged:bool=False

import json,time
from datetime import datetime,timezone,timedelta
from urllib.parse import quote,urlencode
from northwind.compliance import POLICIES,aggregate,RUBRIC_VERSION
from synth.assets import corpus,fingerprint,owned_target,state_path,REQUEST,OWNER
from synth.receipt import RunReceipt
from synth.cohorts import identities,expected_cohorts,read_items,item_matches
from .experiment_runner import exact_prompt
from .judge_execution import call_judge
from .judges import pages,plain,scalar


def freeze(adapter):
    identity=identities(adapter.cfg);history=RunReceipt.load();owned_target(adapter.cfg,vars(history))
    receipt=json.loads(state_path('asset-receipt.json').read_text());owned_target(adapter.cfg,receipt)
    if (receipt.get('history_run_id')!=history.history_run_id or receipt.get('corpus_hash')!=identity['corpus_hash'] or
        receipt.get('project_id')!=identity['project_id']):raise ValueError('Conduct receipt does not match imported history')
    cohort=expected_cohorts(history)['conduct'];bound=receipt['datasets']['conduct'];expected={r['id']:r for r in cohort['items']}
    client=adapter.langfuse();snapshot=datetime.fromisoformat(bound['snapshot_at'])
    actual=read_items(client,cohort['name'],version=snapshot)
    if (bound['name']!=cohort['name'] or bound['cohort_hash']!=cohort['metadata']['cohort_hash'] or
        bound['item_ids']!=list(expected) or set(actual)!=set(expected) or
        any(not item_matches(actual[k],v,bound['id']) for k,v in expected.items())):
        raise ValueError('Conduct cohort membership, source evidence or version changed')
    dataset=client.get_dataset(cohort['name'],version=snapshot)
    if dataset.id!=bound['id'] or dataset.metadata!=cohort['metadata'] or {i.id for i in dataset.items}!=set(expected):
        raise ValueError('Conduct dataset changed during preparation')
    rubrics={}
    for key in ['conduct-v2-single','conduct-legacy',*['conduct-v2-'+p.id.lower() for p in POLICIES]]:
        ref=identity['prompts'][key];prompt=exact_prompt(client,ref['name'],ref['version'])
        if prompt.prompt!=corpus()['prompts'][key] or OWNER not in prompt.tags:raise ValueError('Exact conduct rubric changed')
        rubrics[key]=prompt
    scope=dict(dataset=bound,history_run_id=history.history_run_id,project_id=identity['project_id'],
        cohort_hash=bound['cohort_hash'],rubric_version=RUBRIC_VERSION,provider=adapter.llm().provider,
        reference_labels='constructed',independent_human_review=False,
        model_policy={'temperature':'provider-default','max_output_tokens':500,'provider_retries':0,'request_timeout_seconds':30},
        rubrics={k:dict(name=p.name,version=p.version,content_hash=fingerprint(p.prompt),
            url=identity['prompts'][k]['url']) for k,p in rubrics.items()},
        frozen={k:dict(input=v['input'],expected=v['expected_output'],input_hash=fingerprint(v['input']),
            source_trace_id=v['source_trace_id'],source_observation_id=v['source_observation_id'],case_id=v['metadata']['case_id']) for k,v in expected.items()})
    return dataset,rubrics,scope


def measurements(run):
    out={};expected=run.get('scope',{}).get('frozen',{})
    for arm,data in run['arms'].items():
        rows=data['rows'];done=[r for r in rows if r.get('finished')];known=[r for r in done if r.get('score') is not None]
        references=[r for r in known if aggregate(expected[r['item_id']]['expected']).score is not None]
        violation=[r for r in known if aggregate(expected[r['item_id']]['expected']).score==0]
        safe=[r for r in known if aggregate(expected[r['item_id']]['expected']).score in (0.5,1)]
        calls=[c for r in rows for c in r['calls'].values()]
        complete_usage=(len(done)==len(expected) and all(r.get('usage') is not None for r in done))
        usage={k:sum(c['usage'][k] for c in calls if c.get('usage') is not None) for k in ('input','output')}
        out[arm]=dict(total=len(expected),completed=len(done),resolved=len(known),unresolved=len(expected)-len(known),
            invalid=sum(not r['valid'] for r in done),reference_unresolved=sum(aggregate(r['expected']).score is None for r in expected.values()),
            known_reference_resolved=len(references),agreement_matches=sum(r['score']==aggregate(expected[r['item_id']]['expected']).score for r in references),
            missed_violations=sum(r['score']!=0 for r in violation),resolved_violation_cases=len(violation),
            unresolved_violation_cases=sum(aggregate(v['expected']).score==0 and not any(r['item_id']==k for r in known) for k,v in expected.items()),
            false_alarms=sum(r['score']==0 for r in safe),resolved_non_violation_cases=len(safe),
            usage=usage if complete_usage else None,observed_usage=usage,usage_calls_known=sum(c.get('usage') is not None for c in calls),
            calls_started=data.get('calls_started',0),calls_reported=len(calls),latency_ms=sum(c['latency_ms'] for c in calls),
            latency_complete=len(done)==len(expected) and all(not r.get('error') for r in done))
    left={r['item_id']:r for r in run['arms']['single']['rows'] if r.get('finished') and r.get('score') is not None}
    right={r['item_id']:r for r in run['arms']['split']['rows'] if r.get('finished') and r.get('score') is not None}
    paired=set(left)&set(right)
    out['matched_agreement']={'resolved_pairs':len(paired),'matching':sum(left[k]['score']==right[k]['score'] for k in paired)}
    return out


class ConductReplay:
    @staticmethod
    def initial(body):
        return dict(arms=['single','split']+(['legacy'] if body.include_legacy else []),
            limits=dict(cases_per_arm=22,total_dispatches=154 if body.include_legacy else 132,
                deadline_seconds=600,concurrency=1),gate={'status':'incomplete'})

    def __init__(self,control,run,body,cancel):
        self.control,self.adapter,self.run,self.body,self.cancel=control,control.adapter,run,body,cancel
        self.deadline=time.monotonic()+600

    def check(self):
        if self.cancel.is_set():raise ValueError('cancelled')
        if time.monotonic()>self.deadline:raise ValueError('deadline_reached')
        if self.run['dispatches']>=self.run['limits']['total_dispatches']:raise ValueError('dispatch_limit_reached')

    def item(self,item,arm,rubrics):
        self.check();client=self.adapter.langfuse();frozen=self.run['scope']['frozen'][item.id]
        row=dict(item_id=item.id,case_id=frozen['case_id'],input_hash=frozen['input_hash'],calls={},score=None,valid=False,
            finished=False,verified=False,error=None,trace_id=client.get_current_trace_id(),observation_id=client.get_current_observation_id(),
            source_trace_id=frozen['source_trace_id'],source_observation_id=frozen['source_observation_id'],
            reference_labels='constructed',usage=None)
        self.run['arms'][arm]['rows'].append(row);self.control.persist()
        try:
            keys=['conduct-v2-'+p.id.lower() for p in POLICIES] if arm=='split' else ['conduct-v2-single' if arm=='single' else 'conduct-legacy']
            def dispatched():
                self.run['dispatches']+=1
                self.run['arms'][arm]['calls_started']=self.run['arms'][arm].get('calls_started',0)+1
                self.control.persist()
            for key in keys:
                self.check();prompt=rubrics[key]
                call=call_judge(self.adapter,model=self.body.judge_model,prompt=prompt,inputs=frozen['input'],
                    name='comparison-'+key,check=self.check,on_dispatch=dispatched,
                    **({'categories':next(p.categories for p in POLICIES if key=='conduct-v2-'+p.id.lower())}
                       if arm=='split' else {'score_values':[0,0.5,1,None] if arm=='single' else [0,0.5,1]}))
                row['calls'][key]={**call,'prompt_name':prompt.name,'prompt_version':prompt.version,
                    'prompt_input_hash':fingerprint(prompt.compile(**frozen['input']))}
                self.control.persist()
            if arm=='split':
                labels={p.id:row['calls']['conduct-v2-'+p.id.lower()]['verdict'] for p in POLICIES}
                result=aggregate(labels);row.update(score=result.score,valid=all(v is not None for v in labels.values()),
                    policies=labels,status=result.status,coverage=result.coverage)
            else:
                measured=row['calls'][keys[0]];value,valid=measured['score'],measured['output_valid'];row.update(score=value,valid=valid,
                    policies={},status='invalid' if not valid else 'unresolved' if value is None else 'resolved')
            row['usage']={k:sum(c['usage'][k] for c in row['calls'].values()) for k in ('input','output')} if all(c['usage'] is not None for c in row['calls'].values()) else None
            row['finished']=True
            self.scores(row,arm)
            row['native_output']={k:v for k,v in row.items() if k not in ('verified','native_output')}
            from northwind.masking import CONDUCT_EVIDENCE_SCHEMA
            client.update_current_span(metadata={'northwind_evidence_schema':CONDUCT_EVIDENCE_SCHEMA})
            return row['native_output']
        except Exception as exc:
            row['error']=str(exc) if isinstance(exc,ValueError) and str(exc) in ('cancelled','deadline_reached','dispatch_limit_reached') else type(exc).__name__
            self.cancel.set();raise
        finally:
            self.run['metrics']=measurements(self.run);self.control.persist()

    def scores(self,row,arm):
        values={'judge-comparison-resolved':float(row['score'] is not None)}
        if row['score'] is not None:values['judge-comparison-value']=row['score']
        row['scores']=[]
        for name,value in values.items():
            sid='nw-conduct-'+fingerprint([self.run['id'],arm,row['item_id'],name])
            self.adapter.langfuse().create_score(score_id=sid,name=name,value=value,data_type='NUMERIC',
                trace_id=row['trace_id'],observation_id=row['observation_id'],
                metadata={'comparison_id':self.run['id'],'rubric_version':RUBRIC_VERSION,'reference_labels':'constructed'})
            row['scores'].append(dict(id=sid,name=name,value=value))

    def execute(self):
        terminal='awaiting_readback'
        try:
            self.check()
            dataset,rubrics,scope=freeze(self.adapter);scope['model']=self.body.judge_model
            self.run.update(scope=scope,expected=list(scope['frozen']))
            for arm,value in self.run['arms'].items():
                self.check()
                from opentelemetry import context
                token=context.attach(context.Context())
                try:
                    dataset.run_experiment(name='Northwind matched conduct comparison',run_name=self.run['id']+'-'+arm,
                        description='Frozen constructed references; legacy rubric is an unmatched optional control.',
                        task=lambda *,item,**_:self.item(item,arm,rubrics),max_concurrency=1,experiment_id=value['id'],
                        metadata={'comparison_id':self.run['id'],'kind':'conduct','arm':arm,'provider':scope['provider'],
                            'model':scope['model'],'rubrics':scope['rubrics'],'cohort_hash':scope['cohort_hash'],
                            'reference_labels':'constructed','history_run_id':scope['history_run_id']})
                finally:context.detach(token)
                value['url']=self.adapter.base_url+'/project/'+quote(scope['project_id'],safe='')+'/experiments/results?'+urlencode({'baseline':value['id']})
                if self.cancel.is_set():break
            if self.cancel.is_set():terminal='cancelled' if not any(r.get('error') not in (None,'cancelled') for a in self.run['arms'].values() for r in a['rows']) else 'runner_failed'
        except Exception as exc:
            terminal='cancelled' if self.cancel.is_set() and str(exc)=='cancelled' else 'runner_failed'
            self.run['error']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        finally:
            self.adapter.langfuse().flush();self.run['metrics']=measurements(self.run)
            self.reset_readback(self.run);self.control.persist()
        return terminal

    @staticmethod
    def reset_readback(run):
        expected=len(run.get('expected',[]))*len(run['arms'])
        verified=sum(r.get('verified',False) for arm in run['arms'].values() for r in arm['rows'])
        run['gate']={'status':'verified' if expected and verified==expected else 'incomplete','expected':expected,'verified':verified,
            'meaning':'Evidence completeness only; no quality threshold or promotion approval.'}

    @staticmethod
    def readback(adapter,run):
        client=adapter.langfuse();scope=run['scope'];dataset=scope['dataset']
        start=datetime.fromisoformat(run['created_at'])-timedelta(seconds=1)
        for arm,data in run['arms'].items():
            items=pages(client.api.experiments.list_items,from_start_time=start,experiment_id=data['id'],fields='core,dataset,io,metadata,experimentMetadata')
            byid={r.experiment_item_id:r for r in items}
            if len(items)!=len(byid) or set(byid)-set(run['expected']):raise ValueError('Unexpected or duplicate native comparison items')
            for row in data['rows']:
                row['verified']=False;n=byid.get(row['item_id'])
                if not row.get('finished') or row.get('error') or not n:continue
                if (n.trace_id!=row['trace_id'] or n.experiment_id!=data['id'] or n.experiment_dataset_id!=dataset['id'] or
                    n.experiment_item_version!=datetime.fromisoformat(dataset['snapshot_at']) or
                    scalar(n.expected_output)!=scope['frozen'][row['item_id']]['expected'] or
                    any((n.experiment_metadata or {}).get(k)!=v for k,v in {'comparison_id':run['id'],'kind':'conduct','arm':arm,
                        'provider':scope['provider'],'model':scope['model'],'rubrics':scope['rubrics'],'cohort_hash':scope['cohort_hash'],
                        'reference_labels':'constructed','history_run_id':scope['history_run_id']}.items()) or scalar(n.input)!=scope['frozen'][row['item_id']]['input'] or
                    scalar(n.output)!=row['native_output']):continue
                observations=pages(client.api.observations.get_many,trace_id=row['trace_id'],fields='basic,io,metadata,model,usage,prompt,trace_context')
                byobs={o.id:plain(o) for o in observations}
                def generation_matches(call):
                    o=byobs.get(call['generation_id'],{})
                    return (o.get('type')=='GENERATION' and o.get('traceId')==row['trace_id'] and
                        o.get('parentObservationId')==row['observation_id'] and o.get('model')==scope['model'] and
                        (call['usage'] is None or all((o.get('usageDetails') or {}).get(k)==v for k,v in call['usage'].items())) and
                        scalar((o.get('metadata') or {}).get('provider_usage_reported'))==(call['usage'] is not None) and
                        o.get('promptName')==call['prompt_name'] and o.get('promptVersion')==call['prompt_version'] and
                        fingerprint(scalar(o.get('input')))==call['prompt_input_hash'] and (o.get('output')==call['text'] or scalar(o.get('output'))==call['text']))
                if not all(generation_matches(c) for c in row['calls'].values()):continue
                scores=pages(client.api.scores.get_many,trace_id=row['trace_id'],fields='subject,details');actual={s.id:plain(s) for s in scores}
                def score_matches(s):
                    found=actual.get(s['id'],{});subject=found.get('subject',{})
                    return found.get('source')=='API' and found.get('name')==s['name'] and found.get('value')==s['value'] and subject.get('kind')=='observation' and subject.get('id')==row['observation_id'] and subject.get('traceId')==row['trace_id']
                row['verified']=all(score_matches(s) for s in row['scores'])
        ConductReplay.reset_readback(run)
        if run['status']=='awaiting_readback' and run['gate']['status']=='verified':run['status']='completed'
        return run


def mount_conduct(app,controls):
    from fastapi.responses import HTMLResponse
    from langfuse_synth_core.live import paths
    from html import escape
    from pathlib import Path
    controls.strategies['conduct']=ConductReplay
    @app.get('/manage/experiments/conduct',response_class=HTMLResponse)
    async def page():
        controls.required()
        return Path('demos/bank-assistant/portal/static/conduct.html').read_text().replace('__PREFIX__',escape(paths.base_path(),quote=True))
    @app.get('/manage/experiments/conduct/status')
    async def status():
        state=controls.status()
        return {**state,'provider':controls.adapter.llm().provider,'target':controls.adapter.base_url}
    @app.post('/manage/experiments/conduct/start')
    async def start(body:ConductRequest):return await controls.start(body,kind='conduct')
