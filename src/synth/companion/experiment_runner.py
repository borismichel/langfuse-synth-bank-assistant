"""Fixed application replay and exact native evidence; no moving prompt labels."""
from __future__ import annotations
import asyncio,json,time
from datetime import datetime,timezone,timedelta
from uuid import uuid4
from urllib.parse import quote, urlencode
from northwind import agent,evals,prompts
from northwind.binding import sign
from northwind.compliance import POLICIES,aggregate,RUBRIC_VERSION
from synth.assets import corpus,fingerprint,owned_target,state_path,REQUEST,OWNER
from synth.cohorts import identities,expected_cohorts,read_items,item_matches
from synth.receipt import RunReceipt
from .judge_execution import call_judge_async
from .experiment_gate import assess,VERSION
from .judges import pages,plain,scalar

POLICIES_BY_NAME={
    'current':{'retrieval_k':3,'retrieval_min_score':.08,'tx_default_days':60},
    'short-lookback':{'retrieval_k':3,'retrieval_min_score':.08,'tx_default_days':30},
    'broad-retrieval':{'retrieval_k':8,'retrieval_min_score':0,'tx_default_days':60}}


def exact_prompt(client,name,version):
    result=client.get_prompt(name,version=version,cache_ttl_seconds=0,max_retries=0,fetch_timeout_seconds=15)
    if result.version!=version or result.is_fallback or not isinstance(result.prompt,str):
        raise ValueError('Exact published text prompt is missing')
    return result


def prepare_prompts(adapter):
    """Create only missing known versions; never move a production label."""
    client=adapter.langfuse();name=prompts.PROMPT_NAME
    latest=client.get_prompt(name,label='latest',cache_ttl_seconds=0,max_retries=0,fetch_timeout_seconds=15)
    if latest.version>50:raise ValueError('Prompt history exceeds 50 versions; select exact existing versions in Langfuse')
    deadline=time.monotonic()+120
    existing=[]
    for version in range(1,latest.version+1):
        if time.monotonic()>deadline:raise ValueError('Prompt preparation deadline reached; retry after inspecting native versions')
        existing.append(exact_prompt(client,name,version))
    result={}
    for key,text in [('control',prompts.V5_RELEASE),('regression',prompts.V3_REGRESSION)]:
        matches=[p for p in existing if p.prompt==text]
        if matches:row=matches[-1]
        else:
            # If publication is uncertain, the next explicit prepare scans before creating.
            row=client.create_prompt(name=name,prompt=text,type='text',tags=[OWNER,'application-experiment'],labels=[])
            row=exact_prompt(client,name,row.version)
        if row.prompt!=text:raise ValueError('Prepared prompt content differs from the shipped candidate')
        result[key]={'name':name,'version':row.version,'content_hash':fingerprint(text)}
    return result


def freeze(adapter,body):
    cfg=adapter.cfg;identity=identities(cfg);history=RunReceipt.load()
    owned_target(cfg,vars(history))
    receipt=json.loads(state_path('asset-receipt.json').read_text());owned_target(cfg,receipt)
    if (receipt.get('history_run_id')!=history.history_run_id or receipt.get('corpus_hash')!=identity['corpus_hash'] or
        receipt.get('project_id')!=identity['project_id']):raise ValueError('Fixed cohort receipt differs from the current imported history')
    cohort=expected_cohorts(history)[body.cohort];bound=receipt['datasets'][body.cohort]
    client=adapter.langfuse();version=datetime.fromisoformat(bound['snapshot_at'])
    rows=read_items(client,cohort['name'],version=version)
    expected={i['id']:i for i in cohort['items']}
    if (bound['name']!=cohort['name'] or bound['cohort_hash']!=cohort['metadata']['cohort_hash'] or
        bound['item_ids']!=list(expected) or set(rows)!=set(expected) or
        any(not item_matches(rows[i],v,bound['id']) for i,v in expected.items())):
        raise ValueError('Fixed cohort membership, source relationship or snapshot changed')
    dataset=client.get_dataset(cohort['name'],version=version)
    if dataset.id!=bound['id'] or dataset.metadata!=cohort['metadata'] or {i.id for i in dataset.items}!=set(expected):
        raise ValueError('Dataset changed during preparation')
    production=client.get_prompt(prompts.PROMPT_NAME,label='production',cache_ttl_seconds=0,max_retries=0,fetch_timeout_seconds=15)
    if production.is_fallback:raise ValueError('Current production prompt is unavailable')
    frozen={arm:exact_prompt(client,prompts.PROMPT_NAME,getattr(body,arm+'_version')) for arm in ('baseline','candidate')}
    rubrics={}
    for policy in POLICIES:
        key='conduct-v2-'+policy.id.lower();ref=identity['prompts'][key]
        row=exact_prompt(client,ref['name'],ref['version'])
        if row.prompt!=corpus()['prompts'][key] or OWNER not in row.tags:raise ValueError('Prepared conduct rubric changed')
        rubrics[policy.id]=row
    return dataset,frozen,rubrics,dict(dataset=bound,history_run_id=history.history_run_id,
        project_id=identity['project_id'],corpus_hash=identity['corpus_hash'],rubric_version=RUBRIC_VERSION,
        gate_version=VERSION,required_checks={i.metadata['case_id']:sorted([*checks(i,{'answer':'','banking_actions':[]}), 'conduct']) for i in dataset.items},production_at_start={'name':production.name,'version':production.version,'content_hash':fingerprint(production.prompt)},primary_provider=adapter.llm().provider,primary_model=adapter.llm().model,
        prompts={arm:dict(name=p.name,version=p.version,content_hash=fingerprint(p.prompt)) for arm,p in frozen.items()},
        rubrics={key:dict(name=p.name,version=p.version,content_hash=fingerprint(p.prompt)) for key,p in rubrics.items()})


def checks(item,result):
    kwargs=dict(input=item.input,output=result,expected_output=item.expected_output,metadata=item.metadata)
    selected=[evals.language_match,evals.formal_register,evals.no_unsolicited_upsell,
              evals.must_include,evals.source_recall,evals.cites_expected_source]
    if item.metadata['cohort']=='redteam':selected=[evals.language_match,evals.refused]
    measured={}
    for fn in selected:
        score=fn(**kwargs)
        if score!=[]:measured[score.name]=score.value
    if item.metadata['cohort']=='disputes':
        accepted=set(item.metadata.get('accepted_transactions') or [item.metadata.get('expected_transaction')])
        actual=[a for a in result['banking_actions'] if a.get('status')=='opened']
        measured['dispute-correct-transaction']=float(bool(actual) and all(
            a.get('arguments',{}).get('transaction_id') in accepted for a in actual))
    return measured


class Replay:
    def __init__(self,control,run,body,cancel):
        self.control,self.adapter,self.banking,self.run,self.body,self.cancel=control,control.adapter,control.banking,run,body,cancel
        self.deadline=time.monotonic()+600;self.dispatches=0;self.case_dispatches=0
        self.loop=asyncio.get_running_loop()

    def prepare(self):
        return freeze(self.adapter,self.body)

    def binding(self,arm):
        return {'provider':self.adapter.llm().provider,'model':self.run['scope']['primary_model'],'role':'primary'}

    def assessment(self):
        return assess(self.run.get('expected',[]),self.run['arms']['baseline']['rows'],self.run['arms']['candidate']['rows'],required=self.run.get('scope',{}).get('required_checks'))

    def check(self):
        if self.cancel.is_set():raise ValueError('cancelled')
        if time.monotonic()>self.deadline:raise ValueError('deadline_reached')
        if self.dispatches>=480:raise ValueError('dispatch_limit_reached')

    def dispatched(self):
        self.check()
        self.dispatches+=1;self.run['dispatches']=self.dispatches;self.control.persist()

    async def permit(self,provider=None):
        self.check()
        if self.case_dispatches>=10:raise ValueError('case_dispatch_limit_reached')
        await asyncio.to_thread(self.adapter.check_spending,provider or self.adapter.llm().provider)
        self.check();self.case_dispatches+=1;self.dispatched()

    async def item(self,*,item,arm,prompt,rubrics,experiment_id):
        self.check();self.case_dispatches=0
        selected = self.binding(arm)
        case=item.metadata['case_id'];scenario=uuid4().hex;session='experiment-'+uuid4().hex
        binding=dict(instance=self.banking.instance,epoch=self.banking.epoch,scenario_id=scenario,
            customer_id=item.input.get('customer_id','C-1001'),session_id=session,request_id=uuid4().hex,revision=0)
        row=dict(case_id=case,item_id=item.id,experiment_id=experiment_id,checks={},usage=None,
            verified=False,judge_results={},error=None)
        self.run['arms'][arm]['rows'].append(row);self.control.persist()
        try:
            async def turn(message,history=None,confirmation=None):
                self.check()
                return await agent.run_turn(message,customer_id=binding['customer_id'],session_id=session,
                    history=history,channel='experiment',trace_name='northwind-application-experiment',
                    exact_prompt=prompt,model=selected['model'],llm_role=None if selected['role']=='primary' else selected['role'],run_policy=POLICIES_BY_NAME['current' if arm=='baseline' else self.body.candidate_policy],
                    before_model_call=lambda:self.permit(selected['provider']),banking_binding=sign(binding,self.banking.secret),confirmation=confirmation,
                    tags=['source:depot','scope:application-experiment'],extra_metadata={'live_instance_id':self.banking.instance,
                    'comparison_id':self.run['id'],'experiment_case':case,'experiment_arm':arm,
                    'application_provider':selected['provider'],'application_model':selected['model'],'application_role':selected['role']})
            result=await turn(item.input['question']);turns=[result]
            if result['error']:
                row.update(provider=selected['provider'],model=selected['model'],role=selected['role'],provider_error=result['error'],trace_id=result['trace_id'],observation_id=result['observation_id'])
                raise ValueError('banking_runner_failed')
            if self.body.cohort=='disputes':
                pending=[a for a in result['banking_actions'] if a.get('status')=='confirmation_required' and a.get('tool')=='open_dispute']
                accepted=set(item.metadata.get('accepted_transactions') or [item.metadata.get('expected_transaction')])
                if len(pending)==1 and pending[0]['arguments'].get('transaction_id') in accepted:
                    binding.update(request_id=uuid4().hex,confirmation_id=pending[0]['action_id'])
                    result=await turn(item.metadata.get('confirm_reply','Yes, open this dispute.'),
                        history=[{'role':'user','content':item.input['question']},{'role':'assistant','content':result['answer']}],
                        confirmation=pending[0]);turns.append(result)
                    if result['error']:raise ValueError('banking_confirmation_runner_failed')
            row.update(answer=result['answer'],query=result['query'],context=result['context'],trace_id=result['trace_id'],
                observation_id=result['observation_id'],trace_url=result['trace_url'],prompt_version=result['prompt_version'],
                model=result['model'],provider=selected['provider'],role=selected['role'],banking_actions=result['banking_actions'],tools_used=result['tools_used'],
                latency_ms=sum(r['latency_ms'] for r in turns),turns=len(turns),checks=checks(item,result),
                usage={k:sum(r['usage'][k] for r in turns) for k in ('input','output')} if all(r['usage'] is not None for r in turns) else None)
            labels=await self.judge_policies(row,rubrics)
            combined=aggregate(labels);row['checks']['conduct']=None if combined.unresolved else combined.score
            row['conduct_status']=combined.status
            self.scores(row)
            from northwind.masking import APPLICATION_EVIDENCE_SCHEMA
            self.adapter.langfuse().update_current_span(metadata={'northwind_evidence_schema':APPLICATION_EVIDENCE_SCHEMA})
            return {key:row[key] for key in ('case_id','answer','trace_id','observation_id','checks','usage')}
        except Exception as exc:
            row['error']=str(exc) if isinstance(exc,ValueError) and str(exc) in (
                'cancelled','deadline_reached','dispatch_limit_reached','case_dispatch_limit_reached',
                'banking_runner_failed','banking_confirmation_runner_failed','judge_output_invalid') else type(exc).__name__
            self.cancel.set() # A dispatch/transport failure stops new paid work; no automatic retry.
            raise
        finally:self.control.persist()

    async def judge_policies(self,row,rubrics):
        """Two owned HTTP tasks for one frozen answer; stop and drain before leaving it."""
        policies=iter(POLICIES)
        inputs={k:row[k] for k in ('query','context')}|{'generation':row['answer']}
        labels={}
        async def worker():
            try:
                for policy in policies:
                    self.check()
                    def record(result):
                        row['judge_results'][policy.id]=result
                        self.control.persist()
                    with self.adapter.langfuse().start_as_current_observation(name='experiment-policy-evaluation',
                        trace_context={'trace_id':row['trace_id'],'parent_span_id':row['observation_id']}):
                        judged=await call_judge_async(self.adapter,model=self.body.judge_model,prompt=rubrics[policy.id],
                            inputs=inputs,name=policy.name,check=self.check,on_dispatch=self.dispatched,
                            record=record,categories=policy.categories,deadline=self.deadline)
                    if not judged['output_valid']:
                        self.cancel.set()
                        raise ValueError('judge_output_invalid')
                    labels[policy.id]=judged['verdict']
            except BaseException:
                self.cancel.set()
                raise
        async def stopped():
            while True:
                self.check()
                await asyncio.sleep(.02)
        workers=[asyncio.create_task(worker()) for _ in range(2)]
        work=asyncio.gather(*workers)
        stop=asyncio.create_task(stopped())
        try:
            done,_=await asyncio.wait((work,stop),return_when=asyncio.FIRST_COMPLETED)
            if work in done:await work
            else:await stop
            return labels
        except BaseException:
            self.cancel.set()
            raise
        finally:
            stop.cancel()
            for task in workers:
                if not task.done():task.cancel()
            await asyncio.gather(*workers,stop,return_exceptions=True)
            # Consume a possible secondary cancellation/error from the group too.
            if not work.done():work.cancel()
            await asyncio.gather(work,return_exceptions=True)

    def scores(self,row):
        client=self.adapter.langfuse();row['scores']=[]
        stamp=datetime.now(timezone.utc);stamp=stamp.replace(microsecond=stamp.microsecond//1000*1000)
        for name,value in row['checks'].items():
            if value is None:continue
            identity='nw-exp-'+fingerprint([self.run['id'],row['experiment_id'],row['case_id'],name])
            score=dict(score_id=identity,name='application-'+name,value=value,data_type='NUMERIC',
                trace_id=row['trace_id'],observation_id=row['observation_id'],timestamp=stamp,
                metadata={'comparison_id':self.run['id'],'gate_version':VERSION,'case_id':row['case_id']})
            client.create_score(**score)
            row['scores'].append(dict(id=identity,name=score['name'],value=value,timestamp=stamp.isoformat()))

    def execute(self):
        try:
            self.check()
            if not callable(getattr(self.adapter,'check_spending',None)):raise ValueError('Operation spending checks are unavailable')
            dataset,frozen,rubrics,scope=self.prepare()
            self.run['scope']=scope;self.run['expected']=[i.metadata['case_id'] for i in dataset.items]
            for arm in ('baseline','candidate'):
                self.check();eid=self.run['arms'][arm]['id']
                async def task(*,item):
                    # The SDK owns its experiment loop; cached native async clients
                    # belong to the Companion loop shared with web and voice turns.
                    # Scheduling copies the SDK item's context, preserving its trace.
                    pending=asyncio.run_coroutine_threadsafe(self.item(item=item,arm=arm,
                        prompt=frozen[arm],rubrics=rubrics,experiment_id=eid),self.loop)
                    return await asyncio.wrap_future(pending)
                from opentelemetry import context as otel_context
                token=otel_context.attach(otel_context.Context())
                try:
                    result=dataset.run_experiment(name='Northwind application comparison',run_name='northwind-'+self.run['id']+'-'+arm,
                        description='Full retrieval and isolated MCP replay; constructed reference labels.',
                        task=task,max_concurrency=1,experiment_id=eid,metadata={**scope,'scheduler':'serial-application-two-judges-v1','comparison_id':self.run['id'],
                        'arm':arm,'judge_model':self.body.judge_model,'policy':POLICIES_BY_NAME['current' if arm=='baseline' else self.body.candidate_policy]})
                finally:otel_context.detach(token)
                # SDK5.0.4 leaves experiment_url unset; use the target4.68 results route.
                self.run['arms'][arm]['url']=self.adapter.base_url+'/project/'+quote(scope['project_id'],safe='')+'/experiments/results?'+urlencode({'baseline':eid})
                self.check()
            status='awaiting_readback'
        except Exception as exc:
            status='cancelled' if self.cancel.is_set() and not any(r.get('error') not in (None,'cancelled') for a in self.run['arms'].values() for r in a['rows']) else 'runner_failed'
            self.run['error']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        finally:
            self.adapter.langfuse().flush()
            self.run['gate']=self.assessment()
            self.control.persist()
        return status


def readback(adapter,run):
    """Read-only bounded refresh; never rerun an item or create missing scores."""
    client=adapter.langfuse();scope=run['scope'];dataset=scope['dataset'];start=datetime.fromisoformat(run['created_at'])-timedelta(seconds=1)
    for arm in run['arms'].values():
        items=pages(client.api.experiments.list_items,from_start_time=start,experiment_id=arm['id'],
            fields='core,dataset,io,metadata',score_limit=50)
        byid={i.experiment_item_id:i for i in items}
        if len(byid)!=len(items) or set(byid)-set(dataset['item_ids']):raise ValueError('Native experiment has duplicate or unexpected items')
        for row in arm['rows']:
            row['verified']=False;native=byid.get(row['item_id'])
            if row.get('error') or not native:continue
            output=scalar(native.output)
            if (native.trace_id!=row['trace_id'] or native.experiment_id!=arm['id'] or native.experiment_dataset_id!=dataset['id'] or
                native.experiment_item_version!=datetime.fromisoformat(dataset['snapshot_at']) or not isinstance(output,dict) or
                any(output.get(k)!=row.get(k) for k in ('case_id','answer','trace_id','observation_id','checks','usage'))):continue
            observations=pages(client.api.observations.get_many,trace_id=row['trace_id'],fields='basic,io,metadata,trace_context',
                expand_metadata='context,prompt_version,comparison_id')
            matches=[o for o in observations if o.id==row['observation_id']]
            if len(matches)!=1:continue
            obs=plain(matches[0]);meta={k:scalar(v) for k,v in (obs.get('metadata') or {}).items()}
            if (obs.get('name')!='northwind-assistant' or obs.get('type')!='AGENT' or obs.get('traceId')!=row['trace_id'] or
                scalar(obs.get('output'))!=row['answer'] or scalar(obs.get('input'))!=row['query'] or
                meta.get('context')!=row['context'] or str(meta.get('prompt_version'))!=str(row['prompt_version']) or
                meta.get('comparison_id')!=run['id']):continue
            scores=pages(client.api.scores.get_many,trace_id=row['trace_id'],fields='subject,details')
            actual={s.id:plain(s) for s in scores}
            def matches_score(expected):
                score=actual.get(expected['id'],{});subject=score.get('subject',{})
                return (score.get('source')=='API' and score.get('name')==expected['name'] and
                    score.get('value')==expected['value'] and subject.get('kind')=='observation' and
                    subject.get('id')==row['observation_id'] and subject.get('traceId')==row['trace_id'])
            row['verified']=bool(row.get('scores')) and all(matches_score(s) for s in row['scores'])
    run['gate']=assess(run['expected'],run['arms']['baseline']['rows'],run['arms']['candidate']['rows'],required=scope.get('required_checks'))
    run['readback_at']=datetime.now(timezone.utc).isoformat()
    if run['status']=='awaiting_readback' and run['gate']['status']!='incomplete':run['status']='completed'
    return run
