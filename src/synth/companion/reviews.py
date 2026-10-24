"""Native human review queues and bounded, provenance-separated calibration."""
from __future__ import annotations
import json
import os
from urllib.parse import quote
from synth.assets import REQUEST, corpus, fingerprint, owned_target, state_path
from synth.cohorts import identities
from .judges import PolicyJudges


RUBRIC='faithfulness-v1'
DESCRIPTION='Faithfulness v1: 1 = every claim supported; 0.5 = minor imprecision; 0 = material unsupported claim. Annotate the delivered answer against its approved policy and tool evidence. Native user annotations only; no scripted reviewers.'


class Reviews:
    def __init__(self,adapter,instance):
        self.adapter=adapter
        self.subjects=PolicyJudges(adapter,instance,os.environ.get('LANGFUSE_TRACING_ENVIRONMENT','production'))
        self.state={'queue':None,'rubric':RUBRIC,'operation':None,'calibration':None}

    @property
    def api(self):return self.adapter.langfuse().api

    def bindings(self):
        identity=identities(self.adapter.cfg)
        receipt=json.loads(state_path('asset-receipt.json').read_text())
        owned_target(self.adapter.cfg,receipt)
        if (receipt.get('schema_version')!=1 or receipt.get('corpus_hash')!=fingerprint(corpus())
                or receipt.get('project_id')!=identity['project_id']):
            raise ValueError('Prepared evaluation assets do not match this target')
        self.identity,self.receipt=identity,receipt
        self.project_url=self.adapter.base_url+'/project/'+quote(identity['project_id'],safe='')
        self.subjects.project_url=self.project_url
        self.name='northwind-review-'+fingerprint([receipt['history_run_id'],RUBRIC])[:20]
        bound=identity['score_definitions']['nw-faithfulness-v1']
        config=self.api.score_configs.get_by_id(bound['id'],request_options=REQUEST)
        if (config.name!='nw-faithfulness-v1' or config.data_type!='NUMERIC' or config.is_archived
                or config.min_value!=0 or config.max_value!=1 or config.description!=bound['description']):
            raise ValueError('Prepared faithfulness score definition changed; no reviews modified')
        self.config_id=config.id
        return receipt

    def paged(self,call,**args):
        rows=[]
        for page in range(1,6):
            self.subjects.budget()
            result=call(page=page,limit=100,request_options=REQUEST,**args)
            rows.extend(result.data)
            if result.meta.total_pages>5:raise ValueError('Review scan exceeds 500 records; no completed work modified')
            if page>=result.meta.total_pages:return rows
        raise ValueError('Incomplete review scan')

    def queue(self,create=False):
        rows=[r for r in self.paged(self.api.annotation_queues.list_queues) if r.name==self.name]
        if len(rows)>1:raise ValueError('Ambiguous review queue; inspect native queues')
        if not rows and create:
            self.api.annotation_queues.create_queue(name=self.name,score_config_ids=[self.config_id],
                description=DESCRIPTION,request_options=REQUEST)
            rows=[r for r in self.paged(self.api.annotation_queues.list_queues) if r.name==self.name]
        if not rows:return None,[]
        if len(rows)!=1 or rows[0].score_config_ids!=[self.config_id] or rows[0].description!=DESCRIPTION:
            raise ValueError('Review queue configuration changed; preserved without modification')
        queue=rows[0]
        items=self.paged(self.api.annotation_queues.list_queue_items,queue_id=queue.id)
        if len({(r.object_type,r.object_id) for r in items})!=len(items):
            raise ValueError('Duplicate queue subjects require native inspection')
        return queue,items

    def refresh(self,pending=10):
        self.subjects.begin();self.bindings()
        queue,items=self.queue()
        self.state['queue']=None if queue is None else {
            'id':queue.id,'url':self.project_url+'/annotation-queues/'+quote(queue.id,safe=''),
            'pending':sum(i.status=='PENDING' for i in items),
            'completed':sum(i.status=='COMPLETED' for i in items),'total':len(items),
            'shortfall':max(0,pending-sum(i.status=='PENDING' for i in items)),
            'rubric':DESCRIPTION,'cohort':'Eligible delivered answers from this deployment; completed queue subjects remain retained.'}
        self.state['rubric']=DESCRIPTION
        if hasattr(self,'calibration_runner'):
            if self.state['calibration'] is None:self.state['calibration']=self.calibration_runner.restore()
            self.state['calibration_history']=self.calibration_runner.history()
        return self.state

    def prepare(self,pending):
        self.subjects.begin();self.bindings()
        queue,items=self.queue(create=True)
        existing={i.object_id for i in items if i.object_type=='OBSERVATION'}
        needed=max(0,pending-sum(i.status=='PENDING' for i in items))
        if needed:
            candidates=self.subjects.inspect()['eligible']
            for subject in [s for s in candidates if s['observation_id'] not in existing][:needed]:
                self.subjects.budget()
                self.api.annotation_queues.create_queue_item(queue.id,object_id=subject['observation_id'],
                    object_type='OBSERVATION',status='PENDING',request_options=REQUEST)
        return self.refresh(pending)

    def references(self):
        from datetime import datetime
        from synth.cohorts import read_items
        bound=self.receipt['datasets']['calibration'];cases=corpus()['cohorts']['calibration']
        if bound['cohort_hash']!=fingerprint(cases):raise ValueError('Calibration cohort hash changed')
        actual=read_items(self.adapter.langfuse(),bound['name'],version=datetime.fromisoformat(bound['snapshot_at'].replace('Z','+00:00')))
        if len(bound['item_ids'])!=len(cases):raise ValueError('Calibration cohort is incomplete')
        rows=[]
        for iid,case in zip(bound['item_ids'],cases):
            item=actual.get(iid)
            if (item is None or item.dataset_id!=bound['id'] or item.status!='ACTIVE'
                    or item.input!=case['input'] or item.expected_output!=case['expected_output']
                    or item.metadata.get('label_provenance')!='constructed'):
                raise ValueError('Fixed calibration snapshot changed; no model call made')
            rows.append(dict(id=iid,input=item.input,expected_output=item.expected_output,
                provenance='constructed',source_url=bound['url'],dataset_id=bound['id'],
                dataset_snapshot=bound['snapshot_at'],reviewer_ids=[],score_ids=[]))
        return rows

    def annotations(self):
        from .judges import pages,plain,scalar
        queue,items=self.queue()
        if queue is None:return [],[]
        owned={i.object_id for i in items if i.object_type=='OBSERVATION'}
        scores=pages(self.api.scores.get_many,budget=self.subjects.budget,name='nw-faithfulness-v1',
            source='ANNOTATION',queue_id=queue.id,fields='subject,details')
        grouped={};unresolved=[]
        for score in scores:
            subject=score.subject
            if (score.source!='ANNOTATION' or score.name!='nw-faithfulness-v1' or score.config_id!=self.config_id or score.queue_id!=queue.id
                    or score.data_type!='NUMERIC' or subject is None
                    or subject.kind!='observation' or not subject.trace_id or subject.id not in owned):continue
            if not score.author_user_id:
                unresolved.append(dict(observation_id=subject.id,trace_id=subject.trace_id,
                    source_url=self.project_url+'/traces/'+quote(subject.trace_id,safe=''),
                    status='reviewer_identity_unavailable',score_ids=[score.id]))
                continue
            grouped.setdefault((subject.trace_id,subject.id),{}).setdefault(score.author_user_id,[]).append(score)
        rows=[]
        for (trace,oid),reviewers in sorted(grouped.items()):
            labels=[];ids=[];invalid=False
            for reviewer,entries in sorted(reviewers.items()):
                latest=max(s.updated_at for s in entries)
                newest=[s for s in entries if s.updated_at==latest]
                values={s.value for s in newest};ids.extend(s.id for s in newest)
                if len(values)!=1 or next(iter(values)) not in (0,0.5,1):invalid=True
                else:labels.append(next(iter(values)))
            url=self.project_url+'/traces/'+quote(trace,safe='')
            if invalid or len(set(labels))!=1:
                unresolved.append(dict(observation_id=oid,trace_id=trace,source_url=url,
                    status='invalid_annotation' if invalid else 'reviewer_disagreement',reviewer_ids=sorted(reviewers),score_ids=sorted(ids)))
                continue
            if len(rows)>=20:
                unresolved.append(dict(observation_id=oid,trace_id=trace,source_url=url,status='review_limit'))
                continue
            found=pages(self.api.observations.get_many,budget=self.subjects.budget,
                trace_id=trace,
                fields='basic,io,metadata,trace_context',expand_metadata='context,judge_eligible,judge_evidence_version,live_instance_id')
            found=[plain(o) for o in found if o.id==oid and o.trace_id==trace]
            if len(found)!=1:
                unresolved.append(dict(observation_id=oid,source_url=url,status='source_unavailable'));continue
            row=found[0];meta={k:scalar(v) for k,v in (row.get('metadata') or {}).items()}
            inputs=dict(query=scalar(row.get('input')),generation=scalar(row.get('output')),context=meta.get('context'))
            if (row.get('name')!='northwind-assistant' or row.get('environment')!=self.subjects.environment
                    or meta.get('live_instance_id')!=self.subjects.instance
                    or str(meta.get('judge_eligible')).lower()!='true'
                    or meta.get('judge_evidence_version')!='northwind-delivered-v1'
                    or not all(isinstance(v,str) and v for v in inputs.values())):
                unresolved.append(dict(observation_id=oid,source_url=url,status='source_ineligible'));continue
            rows.append(dict(id='annotation-'+fingerprint([trace,oid]),input=inputs,expected_output=labels[0],
                provenance='native_annotation',source_url=url,source_trace_id=trace,source_observation_id=oid,
                reviewer_ids=sorted(reviewers),score_ids=sorted(ids)))
        return rows,unresolved

    def plan(self,request_id,model,include_reviews):
        from datetime import datetime,timezone
        self.subjects.begin();self.bindings()
        bound=self.identity['prompts']['faithfulness-v1']
        prompt=self.adapter.langfuse().get_prompt(bound['name'],version=bound['version'],cache_ttl_seconds=0,
            max_retries=0,fetch_timeout_seconds=15)
        if prompt.prompt!=corpus()['prompts']['faithfulness-v1'] or prompt.version!=bound['version']:
            raise ValueError('Prepared calibration rubric changed')
        rows=self.references();genuine,unresolved=self.annotations() if include_reviews else ([],[])
        rows+=genuine
        plan=dict(request_id=request_id,status='planned',provider=self.adapter.llm().provider,model=model,
            max_model_calls=len(rows),constructed_rows=len(rows)-len(genuine),annotation_rows=len(genuine),
            unresolved_reviews=unresolved,rows=rows,rubric_version=prompt.version,rubric_name=prompt.name,
            rubric_url=bound['url'],history_run_id=self.receipt['history_run_id'],
            planned_at=datetime.now(timezone.utc).isoformat(),snapshot_hash=fingerprint(rows),
            cost_scope='Up to one paid call per row, 500 output tokens per call. Current provider hourly-estimate cap checked before every dispatch; this is not a billing reservation. Native managed evaluation rules are separate.',
            native_status='not_started')
        plan['experiment_id']=fingerprint([self.receipt['history_run_id'],request_id,model,plan['provider'],plan['snapshot_hash']])[:32]
        return plan
