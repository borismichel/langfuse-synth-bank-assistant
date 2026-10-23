"""Immutable calibration actions; computed evidence and native readback are separate."""
from __future__ import annotations
import json,os,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
from threading import Event
from langfuse import Evaluation
from synth.assets import fingerprint,REQUEST
from .judge_execution import call_judge


def label(value):
    # Never threshold unknown floats or parse a number out of free prose.
    if isinstance(value,bool):return None
    return value if isinstance(value,(float,int)) and value in (0,0.5,1) else None


def verdict(text):
    try:
        value=json.loads(text)
        return label(value.get('score')) if isinstance(value,dict) else None
    except (ValueError,TypeError):return None


def metrics(rows):
    result={}
    for group in ('constructed','native_annotation'):
        selected=[r for r in rows if r['provenance']==group]
        valid=[r for r in selected if label(r.get('actual')) is not None and label(r['expected_output']) is not None]
        result[group]={'total':len(selected),'valid':len(valid),'invalid_or_unresolved':len(selected)-len(valid),
            'matches':sum(r['actual']==r['expected_output'] for r in valid),
            'accuracy':sum(r['actual']==r['expected_output'] for r in valid)/len(valid) if valid else None}
    return result


class Calibration:
    def __init__(self,reviews):
        self.reviews=reviews;self.cancelled=Event();self.lease=None

    def folder(self):
        r=self.reviews
        key=fingerprint([r.adapter.base_url,r.receipt['public_key'],r.receipt['history_run_id']])
        folder=Path(tempfile.gettempdir())/('northwind-calibration-'+str(os.getuid()))/key
        folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        return folder

    def path(self,request_id):return self.folder()/(fingerprint(request_id)+'.json')

    def save(self,plan):
        path=self.path(plan['request_id']);tmp=path.with_suffix('.tmp')
        fd=os.open(tmp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(plan,stream,sort_keys=True)
        tmp.replace(path)

    def remember(self,plan):
        path=self.path(plan['request_id'])
        if path.exists():
            previous=json.loads(path.read_text())
            if any(previous[k]!=plan[k] for k in ('snapshot_hash','model','provider')):
                raise ValueError('Calibration action identity already has different settings or evidence')
            if previous['status']=='running':
                previous.update(status='interrupted',error='Previous execution has an uncertain outcome. Inspect its native experiment; this action cannot be resumed.')
                self.save(previous)
            return previous
        if len(list(self.folder().glob('*.json')))>=20:
            raise ValueError('This Companion has reached its 20-calibration action limit; retained outcomes require native inspection before a new deployment')
        self.save(plan);return plan

    def history(self):
        rows=[]
        for path in self.folder().glob('*.json'):
            record=json.loads(path.read_text())
            rows.append({k:record.get(k) for k in ('request_id','status','planned_at','model','experiment_url','native_status')})
        return sorted(rows,key=lambda row:row['planned_at'],reverse=True)

    def restore(self):
        history=self.history()
        if not history:return None
        record=json.loads(self.path(history[0]['request_id']).read_text())
        if record['status']=='running':
            record.update(status='interrupted',error='Previous execution needs native inspection; do not resume this action.')
            self.save(record)
        return record

    def claim(self):
        import fcntl
        fd=os.open(self.folder()/'operation.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        self.lease=os.fdopen(fd,'w')
        try:fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close();self.lease=None
            raise ValueError('Another calibration worker owns this target')

    def run(self,plan):
        r=self.reviews;deadline=time.monotonic()+900
        def check():
            if self.cancelled.is_set():raise ValueError('Calibration cancelled before the next model call')
            if time.monotonic()>deadline:raise ValueError('Calibration deadline reached before the next model call')
        try:
            if not callable(getattr(r.adapter,'check_spending',None)):
                raise ValueError('Current operation spending checks are unavailable; no model call made')
            check()
            from synth.assets import corpus
            prompt=r.adapter.langfuse().get_prompt(plan['rubric_name'],version=plan['rubric_version'],
                cache_ttl_seconds=0,max_retries=0,fetch_timeout_seconds=15)
            if prompt.prompt!=corpus()['prompts']['faithfulness-v1']:raise ValueError('Exact calibration rubric no longer matches')
            r.adapter.check_spending(plan['provider'])
            existing=r.api.experiments.list(from_start_time=datetime.fromisoformat(plan['planned_at']),
                id=plan['experiment_id'],limit=1,request_options=REQUEST)
            if existing.data:raise ValueError('Native experiment already exists; this action cannot be resumed')
            plan['outcomes']=[]
            frozen={row['id']:row for row in plan['rows']}
            def task(*,item,**_):
                row=frozen[item['id']]
                outcome={**{k:v for k,v in row.items() if k!='input'},'actual':None,'status':'unresolved'}
                try:
                    check()
                    # Count attempted calls separately from successful results; permits can deny a dispatch.
                    def dispatched():
                        plan['model_calls']+=1;self.save(plan)
                    result=call_judge(r.adapter,model=plan['model'],prompt=prompt,inputs=dict(row['input']),check=check,on_dispatch=dispatched)
                    actual=verdict(result['text'])
                    outcome.update(actual=actual,status='scored' if actual is not None else 'invalid_label',**result)
                except Exception as exc:
                    # Do not return provider bodies, inputs embedded in diagnostics, or credentials.
                    reason=getattr(exc,'reason',None)
                    outcome.update(status=('not_started' if plan.get('halt_reason') else 'cancelled') if self.cancelled.is_set() else 'error',error=str(reason or type(exc).__name__))
                    if not self.cancelled.is_set():
                        plan['halt_reason']=str(reason or type(exc).__name__)
                        plan['error']='Paid execution stopped: '+plan['halt_reason']
                    self.cancelled.set() # No automatic retry or more paid work after an uncertain dispatch.
                plan['outcomes'].append(outcome);plan['completed']+=1
                plan['metrics']=metrics(plan['outcomes']);self.save(plan)
                return {k:v for k,v in outcome.items() if k not in ('expected_output','reviewer_ids','score_ids')}
            def evaluate(*,output,expected_output,**_):
                actual=label(output.get('actual'));expected=label(expected_output)
                if actual is None or expected is None:return []
                return Evaluation(name='judge-exact-match',value=actual==expected,data_type='BOOLEAN')
            def aggregate(*,item_results,**_):
                result=[]
                for group,values in metrics(plan['outcomes']).items():
                    for key in ('valid','invalid_or_unresolved','matches','accuracy'):
                        if values[key] is not None:result.append(Evaluation(name='judge-'+group+'-'+key,value=values[key],data_type='NUMERIC'))
                return result
            data=[dict(id=row['id'],input={**row['input'],'calibration_case_id':row['id']},expected_output=row['expected_output'],
                metadata={'case_id':row['id'],**{k:v for k,v in row.items() if k not in ('id','input','expected_output')}}) for row in plan['rows']]
            from opentelemetry import context
            token=context.attach(context.Context())
            try:
                result=r.adapter.langfuse().run_experiment(name='Northwind faithfulness calibration',
                    run_name='Calibration '+plan['request_id'],experiment_id=plan['experiment_id'],data=data,
                    task=task,evaluators=[evaluate],run_evaluators=[aggregate],max_concurrency=1,
                    metadata={'owner':'northwind-depot','snapshot_hash':plan['snapshot_hash'],'rubric':str(plan['rubric_version']),
                        'judge_model':plan['model'],'judge_provider':plan['provider'],'reference_scope':'constructed-and-native-annotations-separated'})
            finally:context.detach(token)
            r.adapter.langfuse().flush()
            plan.update(status='blocked' if plan.get('halt_reason') else 'cancelled' if self.cancelled.is_set() else 'completed',native_status='pending_readback',
                experiment_url=result.experiment_url,metrics=metrics(plan['outcomes']))
            for outcome,item_result in zip(plan['outcomes'],result.item_results):
                outcome['trace_id']=item_result.trace_id
                outcome['result_url']=r.project_url+'/traces/'+str(item_result.trace_id)
        except Exception as exc:
            plan.update(status='blocked',error=('Spending check: '+exc.reason) if hasattr(exc,'reason') else (str(exc) if isinstance(exc,ValueError) else type(exc).__name__),native_status='unconfirmed')
        finally:
            plan['finished_at']=datetime.now(timezone.utc).isoformat();self.save(plan)
            if self.lease:self.lease.close();self.lease=None

    def readback(self,plan):
        from .judges import pages,scalar
        r=self.reviews;r.subjects.begin()
        if not plan.get('outcomes'):raise ValueError('No computed calibration outcomes to verify')
        start=datetime.fromisoformat(plan['planned_at'])
        experiments=r.api.experiments.list(from_start_time=start,id=plan['experiment_id'],fields='metadata,scores',limit=2,request_options=REQUEST)
        rows=pages(r.api.experiments.list_items,budget=r.subjects.budget,from_start_time=start,
            experiment_id=plan['experiment_id'],fields='core,io,itemMetadata,experimentMetadata,scores')
        plan['native_status']='pending_readback'
        if len(experiments.data)!=1 or experiments.data[0].id!=plan['experiment_id']:
            self.save(plan);return plan
        experiment=experiments.data[0]
        if ((experiment.metadata or {}).get('snapshot_hash')!=plan['snapshot_hash']
                or experiment.item_count!=len(plan['rows']) or len(rows)!=len(plan['rows'])):
            self.save(plan);return plan
        expected={row['id']:row for row in plan['rows']};outcomes={row['id']:row for row in plan['outcomes']};seen=set()
        for native in rows:
            meta=native.experiment_item_metadata or {};iid=meta.get('case_id')
            if iid not in expected or iid in seen:plan['native_status']='conflict';break
            seen.add(iid);wanted=expected[iid];outcome=outcomes[iid]
            output={k:v for k,v in outcome.items() if k not in ('expected_output','reviewer_ids','score_ids','trace_id','result_url')}
            if (native.experiment_id!=plan['experiment_id'] or native.trace_id!=outcome['trace_id']
                    or scalar(native.input)!={**wanted['input'],'calibration_case_id':iid}
                    or scalar(native.expected_output)!=wanted['expected_output'] or scalar(native.output)!=output
                    or meta.get('provenance')!=wanted['provenance'] or meta.get('score_ids')!=wanted['score_ids']
                    or meta.get('reviewer_ids')!=wanted['reviewer_ids']):
                plan['native_status']='conflict';break
        else:
            wanted_scores={}
            for group,values in plan['metrics'].items():
                for key in ('valid','invalid_or_unresolved','matches','accuracy'):
                    if values[key] is not None:wanted_scores['judge-'+group+'-'+key]=values[key]
            actual={}
            for score in experiment.scores or []:
                subject=score.subject
                if score.name in wanted_scores and score.source=='API' and score.data_type=='NUMERIC' and subject and subject.kind=='experiment' and subject.id==plan['experiment_id']:
                    actual.setdefault(score.name,[]).append(score.value)
            if all(actual.get(name)==[value] for name,value in wanted_scores.items()):plan['native_status']='confirmed'
        plan['readback_at']=datetime.now(timezone.utc).isoformat();self.save(plan);return plan
