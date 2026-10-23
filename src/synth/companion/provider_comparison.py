"""Fixed application inputs across explicit provider bindings, with native accounting."""
import copy,json,math
from uuid import uuid4
from pydantic import BaseModel,Field,ConfigDict
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from langfuse_synth_core.live import paths
from .experiment_runner import Replay,freeze,readback as application_readback
from .experiment_gate import assess
from .judges import pages,plain,scalar

VERSION='northwind-provider-comparison-v1'

class ProviderRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    request_id: str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    cohort: str=Field(pattern=r'^(english|spanish|redteam|disputes)$')
    prompt_version: int=Field(ge=1)
    judge_model: str=Field(min_length=1,max_length=120,pattern=r'^[A-Za-z0-9._:/-]+$')
    cost_acknowledged: bool=False
    @property
    def baseline_version(self):return self.prompt_version
    @property
    def candidate_version(self):return self.prompt_version
    @property
    def candidate_policy(self):return 'current'


def bindings(adapter):
    primary=adapter.llm()
    if primary.provider!='anthropic':raise ValueError('Provider comparison requires Anthropic as the deployment primary provider')
    try:comparison=adapter.role_binding('comparison')
    except ValueError:raise ValueError('OpenAI comparison is disabled for this deployment') from None
    if comparison.provider!='openai' or comparison.model!='gpt-4.1-mini':
        raise ValueError('Comparison requires the declared OpenAI gpt-4.1-mini binding')
    # Ready construction is local only. A missing credential/factory fails before either arm spends.
    adapter.native_llm_for('comparison')
    return {'baseline':dict(provider=primary.provider,model=primary.model,role='primary'),
            'candidate':dict(provider=comparison.provider,model=comparison.model,role='comparison')}


def assessment(run):
    result=assess(run.get('expected',[]),run['arms']['baseline']['rows'],run['arms']['candidate']['rows'],
        required=run.get('scope',{}).get('required_checks'),max_token_ratio=None)
    return {**result,'gate_version':VERSION,'promotion_eligible':False,
        'cost_measure':'Native provider-specific application and judge costs; token counts across different models are not a price comparison.'}


class ProviderReplay(Replay):
    @staticmethod
    def initial(body):
        return dict(arms=('baseline','candidate'),limits={'cases_per_arm':16,'arms':2,'application_dispatches_per_case':10,
            'judge_calls_per_case':5,'total_dispatches':480,'deadline_seconds':600,'concurrency':1},
            gate={'status':'incomplete','expected':0,'scored':0,'missing':0,'errors':0,'failed':0,'promotion_eligible':False})
    def prepare(self):
        selected=bindings(self.adapter)
        dataset,prompts,rubrics,scope=freeze(self.adapter,self.body)
        scope.update(bindings=selected,judge={'provider':self.adapter.llm().provider,'model':self.body.judge_model,'role':'primary'},
            comparison_version=VERSION,comparison_policy='current',comparison_scope='provider-only; same exact prompt, retrieval and judges')
        return dataset,prompts,rubrics,scope
    def binding(self,arm):return self.run['scope']['bindings'][arm]
    def assessment(self):return assessment(self.run)
    @staticmethod
    def reset_readback(run):
        for arm in run['arms'].values():
            for row in arm['rows']:row['verified']=False
        run['measurements']={};run['gate']=assessment(run)
    @staticmethod
    def readback(adapter,run):
        # The generic read may mutate its gate; keep partial refreshes off the visible receipt.
        measured=application_readback(adapter,copy.deepcopy(run))
        summaries={}
        for arm_name,arm in measured['arms'].items():
            selected=run['scope']['bindings'][arm_name]
            summary={key:{'generations':0,'usage':{'input':0,'output':0},'native_cost_usd':0.0,'missing':0} for key in ('application','judges')}
            summary.update(provider=selected['provider'],model=selected['model'],latency_ms=0)
            for row in arm['rows']:
                if row.get('error') or not row.get('trace_id'):
                    # An uncertain dispatch or partial judge failure is not zero consumption.
                    for target in (summary['application'],summary['judges']):target['missing']+=1
                    continue
                observations=pages(adapter.langfuse().api.observations.get_many,trace_id=row['trace_id'],
                    fields='basic,metadata,model,usage,prompt',expand_metadata='application_provider,application_model,application_role')
                roots=[plain(o) for o in observations if o.id==row['observation_id']]
                meta={k:scalar(v) for k,v in (roots[0].get('metadata',{}) if len(roots)==1 else {}).items()}
                if any(meta.get('application_'+k)!=selected[k] for k in ('provider','model','role')):row['verified']=False
                generations=[plain(o) for o in observations if str(o.type).upper()=='GENERATION']
                accounting={key:[] for key in ('application','judges')}
                for generation in generations:
                    kind='application' if generation.get('name')=='generate-response' else 'judges'
                    expected_model=selected['model'] if kind=='application' else run['scope']['judge']['model']
                    usage=generation.get('usageDetails') or {};cost=generation.get('totalCost')
                    valid=(generation.get('model')==expected_model and
                        all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input','output')) and
                        type(cost) in (int,float) and math.isfinite(cost) and cost>=0)
                    accounting[kind].append(generation)
                    target=summary[kind];target['generations']+=1
                    if not valid:target['missing']+=1;row['verified']=False
                    else:
                        for key in ('input','output'):target['usage'][key]+=usage[key]
                        target['native_cost_usd']+=cost
                if len(accounting['judges'])!=5:
                    summary['judges']['missing']+=1
                    row['verified']=False
                actual={k:sum((g.get('usageDetails') or {}).get(k,0) for g in accounting['application']) for k in ('input','output')}
                if actual!=row.get('usage') or (not accounting['application'] and row.get('usage')!={'input':0,'output':0}):
                    summary['application']['missing']+=1
                    row['verified']=False
                summary['latency_ms']+=row.get('latency_ms',0)
            for target in (summary['application'],summary['judges']):
                if target['missing']:target['native_cost_usd']=None;target['usage']=None
            summaries[arm_name]=summary
        measured['measurements']=summaries;measured['gate']=assessment(measured)
        run.update(measured)
        return run


def mount_provider_comparison(app,controls):
    controls.strategies['provider']=ProviderReplay
    @app.get('/manage/experiments/providers',response_class=HTMLResponse)
    async def page():
        controls.required()
        from pathlib import Path
        from html import escape
        return Path('demos/bank-assistant/portal/static/providers.html').read_text().replace('__PREFIX__',escape(paths.base_path(),quote=True))
    @app.get('/manage/experiments/providers/status')
    async def status():
        controls.required()
        try:return {'enabled':True,'bindings':bindings(controls.adapter)}
        except ValueError as exc:return {'enabled':False,'reason':str(exc)}
    @app.post('/manage/experiments/providers/start')
    async def start(body:ProviderRequest):
        controls.required()
        try:bindings(controls.adapter)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        return await controls.start(body,kind='provider')
