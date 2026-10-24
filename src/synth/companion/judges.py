"""Explicit, owned managed policy judging through stable Langfuse public APIs."""
from __future__ import annotations
import json, os, time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from threading import Event
from langfuse_synth_core.companion.llm import API_KEY_ENV
from northwind.compliance import POLICIES, RUBRIC_VERSION
from synth.assets import fingerprint, REQUEST
from .reconciliation import newest, summarize

MAPPING=[{'variable':'query','source':'input'},
         {'variable':'context','source':'metadata','jsonPath':'$.context'},
         {'variable':'generation','source':'output'}]
BLOCKED=['risk:prompt_injection','risk:cross_customer_access','risk:social_engineering']


def plain(value):
    return value.dict(exclude_none=True) if hasattr(value,'dict') else value


def pages(call, *, budget=lambda:None, **kwargs):
    rows=[];cursor=None;seen=set()
    for _ in range(5):
        budget()
        result=call(limit=100,cursor=cursor,request_options=REQUEST,**kwargs)
        rows.extend(result.data)
        cursor=getattr(result.meta,'cursor',None)
        if not cursor:return rows
        if cursor in seen:raise ValueError('Nonadvancing target cursor; scan incomplete')
        seen.add(cursor)
    raise ValueError('Target scan exceeds 500 records; narrow the inspection window')


def scalar(value):
    if isinstance(value,str):
        try:return json.loads(value)
        except ValueError:return value
    return value


class PolicyJudges:
    def __init__(self,adapter,instance,environment):
        self.adapter,self.instance,self.environment=adapter,instance,environment
        self.namespace='northwind-'+fingerprint(instance)[:16]
        self.discovered=False;self.identity=None;self.rule=None;self.definitions={};self.receipts={};self.evidence={}
        self.cancelled=Event();self.deadline=float('inf')
        self.project_url='';self.last_error=None;self.checked_at=None;self.results=[];self.coverage=None

    @property
    def provider(self):
        return self.adapter.llm().provider

    def begin(self):
        self.deadline=time.monotonic()+180
        self.last_error=None
        self.budget()

    def budget(self):
        if self.cancelled.is_set():raise ValueError('Companion stopped; activation cancelled before the next target action')
        if time.monotonic()>self.deadline:raise ValueError('Judge operation deadline reached; refresh native status before retry')

    @property
    def request(self):
        self.budget()
        return REQUEST

    def pages(self,call,**kwargs):
        return pages(call,budget=self.budget,**kwargs)

    @property
    def api(self):
        self.budget()
        return self.adapter.langfuse().api

    def filters(self):
        return [
            {'type':'stringOptions','column':'name','operator':'any of','value':['northwind-assistant']},
            {'type':'stringOptions','column':'type','operator':'any of','value':['AGENT']},
            {'type':'stringOptions','column':'traceName','operator':'any of','value':['northwind-assistant','northwind-voice-call']},
            {'type':'stringOptions','column':'environment','operator':'any of','value':[self.environment]},
            {'type':'stringObject','column':'metadata','key':'live_instance_id','operator':'=','value':self.instance},
            {'type':'stringObject','column':'metadata','key':'judge_eligible','operator':'=','value':'true'},
            {'type':'stringObject','column':'metadata','key':'judge_evidence_version','operator':'=','value':'northwind-delivered-v1'},
            {'type':'arrayOptions','column':'tags','operator':'none of','value':BLOCKED}]

    def inspect(self):
        start=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
        window=[{'type':'datetime','column':'startTime','operator':'>=','value':start}]
        def read(filters):
            return self.pages(self.api.observations.get_many,filter=json.dumps(filters+window),
                fields='basic,io,metadata,trace_context',
                expand_metadata='context,judge_eligible,judge_evidence_version,live_instance_id,live_run_id')
        selected=read(self.filters());seen=set();eligible=[]
        for row in selected:
            d=plain(row);meta={k:scalar(v) for k,v in (d.get('metadata') or {}).items()}
            # Independently validate target filtering rather than trusting counts.
            if (d.get('name')!='northwind-assistant' or d.get('type')!='AGENT' or
                d.get('environment')!=self.environment or meta.get('live_instance_id')!=self.instance or
                str(meta.get('judge_eligible')).lower()!='true' or
                meta.get('judge_evidence_version')!='northwind-delivered-v1' or set(d.get('tags') or []) & set(BLOCKED)):
                raise ValueError('Target returned an ineligible judge subject')
            if d['traceId'] in seen:raise ValueError('More than one eligible assistant in a trace')
            seen.add(d['traceId'])
            context=meta.get('context');query=scalar(d.get('input'));answer=scalar(d.get('output'))
            if not all(isinstance(v,str) and v for v in (context,query,answer)):
                raise ValueError('Judge variables do not resolve to the selected delivered answer')
            if not context.startswith('APPROVED POLICY AND BANKING TOOL RESULTS'):
                raise ValueError('Authoritative judge evidence is not marked')
            # Score upserts preserve ID and the server's millisecond timestamp.
            timestamp=d['startTime'].replace(microsecond=d['startTime'].microsecond//1000*1000)
            eligible.append(dict(trace_id=d['traceId'],observation_id=d['id'],query=query,
                timestamp=timestamp,live_run_id=meta.get('live_run_id'),
                context=context,generation=answer,url=self.project_url+'/traces/'+quote(d['traceId'],safe='')))
        scope=[{'type':'stringOptions','column':'name','operator':'any of','value':['northwind-assistant']},
            {'type':'stringObject','column':'metadata','key':'live_instance_id','operator':'=','value':self.instance}]
        controls=read(scope);negative={'blocked':0,'history':0}
        for row in controls:
            d=plain(row);meta={k:scalar(v) for k,v in (d.get('metadata') or {}).items()}
            blocked=bool(set(d.get('tags') or []) & set(BLOCKED));history=d.get('environment')=='synthetic-history'
            if blocked or history:
                if d['traceId'] in seen:raise ValueError('Paid filter includes a negative control')
                negative['blocked']+=blocked;negative['history']+=history
        # History has no live deployment ID; inspect its own negative scope separately.
        historical=self.api.observations.get_many(limit=1,fields='basic,trace_context',
            filter=json.dumps([{'type':'stringOptions','column':'name','operator':'any of','value':['northwind-assistant']},
                {'type':'stringOptions','column':'environment','operator':'any of','value':['synthetic-history']}]),request_options=self.request).data
        if any(row.trace_id in seen for row in historical):raise ValueError('Paid filter includes historical traffic')
        negative['history']=len(historical)
        self.evidence=dict(eligible=eligible,negative=negative,checked_at=datetime.now(timezone.utc).isoformat(),
            scope='Eligible live answers: current deployment, last 24 hours. Blocked controls use that window; historical exclusion uses at most one backdated sample. A zero count means missing observed proof, not a pass.')
        return self.evidence

    def ensure_connection(self,model,*,create=True):
        key=os.environ.get(API_KEY_ENV[self.provider])
        if create and not key:raise ValueError('Selected provider credential is missing')
        name=self.namespace+'-judge-'+self.provider
        rows=[]
        for page in range(1,6):
            result=self.api.llm_connections.list(page=page,limit=100,request_options=self.request)
            rows.extend(result.data)
            if result.meta.total_pages>5:raise ValueError('Connection scan exceeds500 records')
            if page>=result.meta.total_pages:break
        matching=[r for r in rows if r.provider==name]
        if len(matching)>1:raise ValueError('Ambiguous owned model connection')
        if matching:row=matching[0]
        elif not create:raise ValueError('Owned model connection is missing; native inspection required')
        else:row=self.api.llm_connections.upsert(provider=name,adapter=self.provider,secret_key=key,
                    custom_models=[model],with_default_models=False,request_options=self.request)
        if row.adapter!=self.provider or row.custom_models!=[model] or row.with_default_models or row.base_url:
            raise ValueError('Owned model connection differs; existing credentials preserved')
        return dict(id=row.id,provider=name,adapter=self.provider,model=model,status='Configured; execution unverified')

    def definition(self,policy,model,connection):
        body=dict(type='llm_as_judge',name=f'{self.namespace}-{policy.id.lower()}-{fingerprint([model,policy.prompt])[:12]}',
            prompt=policy.prompt,modelConfig={'provider':connection,'model':model},
            variableMapping=MAPPING,outputDefinition=policy.output_definition)
        body['description']='northwind-depot; '+self.instance+'; '+RUBRIC_VERSION+'; '+fingerprint(body)
        return body

    def check_definition(self,row,expected):
        actual=plain(row)
        expected={**expected,'prompt':[{'role':'user','content':expected['prompt']}]}
        # Omitted JSONPath and explicit null are equivalent API encodings.
        if any(actual.get(k)!=v for k,v in expected.items()):
            raise ValueError('Managed evaluator definition drift; existing evaluator preserved')
        if row.version!=1:raise ValueError('Managed evaluator latest version changed')
        return dict(id=row.id,name=row.name,version=row.version,version_id=row.version_id,
            status=row.status,paused_reason=row.paused_reason,url=self.project_url+'/evals/'+quote(row.id,safe=''))

    def check_rule(self,row):
        actual=plain(row)
        expected=self.rule['definition']
        if any(actual.get(k)!=v for k,v in expected.items()):raise ValueError('Owned evaluation rule changed; no update performed')
        return bool(row.enabled)

    def activate(self,model,sampling):
        self.begin()
        budget=self.budget
        self.recover()
        selected=dict(model=model,sampling=sampling)
        if self.identity and self.identity['selection']!=selected:
            raise ValueError('This deployment already owns a different judge configuration; preserve it and use a new deployment')
        project=self.adapter.read_json('/api/public/projects')['data'][0]['id']
        self.project_url=self.adapter.base_url+'/project/'+quote(project,safe='')
        self.inspect()
        if not self.evidence['eligible']:
            raise ValueError('Send a live banking answer first; judge inputs have not been verified')
        connection=self.ensure_connection(model);budget()
        self.identity=dict(selection=selected,connection=connection,rubric=RUBRIC_VERSION,
                           project_id=project,instance=self.instance)
        existing=self.pages(self.api.evaluators.list)
        for policy in POLICIES:
            budget();definition=self.definition(policy,model,connection['provider'])
            matches=[row for row in existing if row.name==definition['name']]
            if len(matches)>1:raise ValueError('Ambiguous owned evaluator name')
            row=matches[0] if matches else self.api.evaluators.create(request=definition,request_options=self.request)
            self.definitions[policy.id]=definition
            self.receipts[policy.id]=dict(policy=policy.id,**self.check_definition(row,definition))
        assignments=[{'evaluatorId':self.receipts[p.id]['id'],'variableMapping':MAPPING} for p in POLICIES]
        definition=dict(name=self.namespace+'-policy-'+fingerprint(selected)[:12],
            sampling=sampling,filter=self.filters(),evaluatorAssignments=assignments)
        matches=[r for r in self.pages(self.api.evaluation_rules.list) if r.name==definition['name']]
        if len(matches)>1:raise ValueError('Ambiguous owned evaluation rule')
        row=matches[0] if matches else self.api.evaluation_rules.create(name=definition['name'],enabled=False,
            sampling=sampling,filter=definition['filter'],evaluator_assignments=assignments,request_options=self.request)
        self.rule={'id':row.id,'definition':definition,'enabled':False}
        self.check_rule(row);budget()
        for policy in POLICIES:
            row=self.api.evaluators.get(self.receipts[policy.id]['id'],request_options=self.request)
            self.receipts[policy.id]=dict(policy=policy.id,**self.check_definition(row,self.definitions[policy.id]))
            if row.status!='active':raise ValueError('Evaluator paused: '+str(row.paused_reason or 'unknown reason'))
        self.rule['enabled']=None  # A lost response cannot be reported as disabled.
        row=self.api.evaluation_rules.update(self.rule['id'],enabled=True,request_options=self.request)
        self.rule['enabled']=self.check_rule(row)
        row=self.api.evaluation_rules.get(self.rule['id'],request_options=self.request)
        self.rule['enabled']=self.check_rule(row)
        if not self.rule['enabled']:raise ValueError('Rule activation was not confirmed')
        return self.snapshot()

    def recover(self):
        """Read-only recovery from exact native definitions; no mutable local receipt."""
        project=self.adapter.read_json('/api/public/projects')['data'][0]['id']
        self.project_url=self.adapter.base_url+'/project/'+quote(project,safe='')
        rules=[r for r in self.pages(self.api.evaluation_rules.list) if r.name.startswith(self.namespace+'-policy-')]
        if len(rules)>1:raise ValueError('Multiple owned rules exist; inspect native rules')
        self.discovered=True
        if not rules:
            self.rule=None
            return
        row=rules[0];actual=plain(row)
        owned={}
        for assignment in actual['evaluatorAssignments']:
            evaluator=self.api.evaluators.get(assignment['evaluatorId'],request_options=self.request)
            body=plain(evaluator);model=body['modelConfig']['model']
            connection=self.namespace+'-judge-'+self.provider
            matches=[p for p in POLICIES if self.definition(p,model,connection)['name']==evaluator.name]
            if len(matches)!=1 or matches[0].id in owned:
                raise ValueError('Owned rule assignments differ; native inspection required')
            policy=matches[0];definition=self.definition(policy,model,connection)
            receipt=dict(policy=policy.id,**self.check_definition(evaluator,definition))
            previous=self.receipts.get(policy.id)
            if previous and (previous['id'],previous['version_id'])!=(receipt['id'],receipt['version_id']):
                raise ValueError('Owned evaluator identity changed')
            owned[policy.id]=(model,definition,receipt)
        if set(owned)!={p.id for p in POLICIES} or len({r[0] for r in owned.values()})!=1:
            raise ValueError('Owned rule must contain all five policy judges with one explicit model')
        selected=dict(model=next(iter(owned.values()))[0],sampling=row.sampling)
        expected=dict(name=self.namespace+'-policy-'+fingerprint(selected)[:12],sampling=row.sampling,
            filter=self.filters(),evaluatorAssignments=[{'evaluatorId':owned[p.id][2]['id'],'variableMapping':MAPPING} for p in POLICIES])
        candidate={'id':row.id,'definition':expected,'enabled':None}
        if any(actual.get(k)!=v for k,v in expected.items()):
            raise ValueError('Owned evaluation rule changed; native inspection required')
        self.rule={**candidate,'enabled':bool(row.enabled)}
        self.definitions={p:values[1] for p,values in owned.items()}
        self.receipts={p:values[2] for p,values in owned.items()}
        self.identity=dict(selection=selected,connection=self.ensure_connection(selected['model'],create=False),
            rubric=RUBRIC_VERSION,project_id=project,instance=self.instance)

    def refresh(self):
        self.begin()
        self.last_error=None
        self.recover()
        self.inspect()
        self.results=[]
        subjects=self.evidence['eligible']
        scores=[]
        if subjects and self.rule:
            # Bounded batches also keep the GET query below typical proxy URL limits.
            for offset in range(0,len(subjects),25):
                batch=subjects[offset:offset+25]
                scores.extend(self.pages(self.api.scores.get_many,fields='details,subject',source='EVAL',
                    name=','.join(r['name'] for r in self.receipts.values()),
                    trace_id=','.join(r['trace_id'] for r in batch),
                    observation_id=','.join(r['observation_id'] for r in batch)))
        for subject in subjects:
            policies=[]
            for policy in POLICIES:
                receipt=self.receipts.get(policy.id);matches=[]
                if receipt:
                    for score in scores:
                        d=plain(score);ref=d.get('subject') or {};meta=d.get('metadata') or {}
                        if (d.get('source')=='EVAL' and
                            d.get('name')==receipt['name'] and d.get('environment')==self.environment and
                            ref=={'kind':'observation','id':subject['observation_id'],'traceId':subject['trace_id']} and
                            meta.get('evaluator_id')==receipt['id'] and meta.get('evaluator_version_id')==receipt['version_id'] and
                            meta.get('evaluation_rule_id')==self.rule['id'] and
                            meta.get('target_trace_id')==subject['trace_id'] and meta.get('target_observation_id')==subject['observation_id']):matches.append(d)
                result=dict(policy=policy.id,state='pending' if receipt else 'not_configured',value=None,paused=bool(receipt and receipt['status']!='active'))
                if receipt and receipt['status']!='active':result['state']='paused'
                if matches:
                    result.update(newest(matches,policy),evaluator_id=receipt['id'],evaluator_version_id=receipt['version_id'])
                policies.append(result)
            self.results.append(dict(trace_id=subject['trace_id'],observation_id=subject['observation_id'],
                url=subject['url'],policies=policies))
        self.coverage=summarize(self.results,self.evidence['negative']['blocked'])
        self.checked_at=datetime.now(timezone.utc).isoformat()
        return self.snapshot()

    def pause(self):
        self.begin()
        if not self.rule:raise ValueError('No owned rule is recorded in this process; inspect native rules before stopping the deployment')
        row=self.api.evaluation_rules.get(self.rule['id'],request_options=self.request);self.check_rule(row)
        self.rule['enabled']=None
        self.api.evaluation_rules.update(self.rule['id'],enabled=False,request_options=self.request)
        row=self.api.evaluation_rules.get(self.rule['id'],request_options=self.request)
        self.rule['enabled']=self.check_rule(row)
        if self.rule['enabled']:raise ValueError('Rule pause was not confirmed')
        return self.snapshot()

    def snapshot(self):
        return dict(provider=self.provider,credential_available=bool(os.environ.get(API_KEY_ENV[self.provider])),identity=self.identity,checked_at=self.checked_at,readback_error=self.last_error,results=self.results,coverage=self.coverage,enabled=self.rule['enabled'] if self.rule else (False if self.discovered else None),
            rule_id=self.rule['id'] if self.rule else None,evaluators=list(self.receipts.values()),
            evidence=self.evidence,rules_url=self.project_url+'/evals/rules' if self.project_url else None,
            lifecycle='Remote rules and queued evaluations can continue after this Companion stops. Pause the owned rule before teardown.',
            result_status='Only exact managed EVAL results count. Missing scores are pending or unsampled, not passes or proof of execution failure. Native evaluator details expose execution errors.')
