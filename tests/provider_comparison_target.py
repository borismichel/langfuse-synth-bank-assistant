"""Anthropic and OpenAI wire contracts with native accounting readback."""
import json
from experiment_target import provider_fixture

def two_providers(env,data,monkeypatch):
    provider_fixture(env,data,monkeypatch)
    env.update(OPENAI_API_KEY='fixture-openai-secret',OPENAI_BASE_URL=env['LANGFUSE_BASE_URL'],
        LLM_ROLES_JSON=json.dumps({'version':1,'bindings':{'comparison':{'provider':'openai','model':'gpt-4.1-mini'}}}))
    prior=data['extension'];data['openai_calls']=[]
    from synth.assets import corpus
    cases=[c for group in corpus()['cohorts'].values() for c in group if isinstance(c['input'],dict) and 'question' in c['input']]
    def extension(method,path,query,body):
        if path.endswith('/operation-permit') and body['provider']=='openai':
            data['permits'].append(body)
            if data.get('deny_openai'):return 409,{'reason':'cap_exhausted'}
            return 200,dict(allowed=True,provider='openai',source='shared',checked_at='2026-10-23T12:00:00+00:00',accounting='hourly-estimate',scope='shared-company')
        if path.endswith('/chat/completions'):
            data['openai_calls'].append(body)
            if data.get('missing_model'):return 404,{'error':{'message':'Fixture unavailable model','type':'invalid_request_error','code':'model_not_found'}}
            question=next(m['content'] for m in body['messages'] if m['role']=='user')
            case=next(c for c in cases if c['input']['question']==question)
            answered=body['messages'][-1]['role']=='tool'
            message=({'role':'assistant','content':case['expected_output']+' '+' '.join('['+s+']' for s in case['metadata'].get('expected_sources',[]))} if answered else
                {'role':'assistant','content':None,'tool_calls':[{'id':'search','type':'function','function':{'name':'search_knowledge_base','arguments':json.dumps({'query':question})}}]})
            if case['metadata'].get('expected_transaction'):
                from experiment_target import dispute_reply
                converted={'system':'\n'.join(m['content'] for m in body['messages'] if m['role'] in ('system','developer')),'messages':[]}
                for msg in body['messages']:
                    if msg['role'] in ('system','developer'):continue
                    if msg['role']=='tool':converted['messages'].append({'role':'user','content':[{'type':'tool_result','tool_use_id':msg['tool_call_id'],'content':msg['content']}]})
                    else:converted['messages'].append({'role':msg['role'],'content':msg.get('content') or ''})
                blocks=dispute_reply(converted,case)
                if blocks[0]['type']=='tool_use':
                    b=blocks[0];message={'role':'assistant','content':None,'tool_calls':[{'id':b['id'],'type':'function','function':{'name':b['name'],'arguments':json.dumps(b['input'])}}]}
                else:message={'role':'assistant','content':blocks[0]['text']}
                answered='tool_calls' not in message
            # The real OpenAI response resolves the configured alias to a dated model.
            result={'id':'chat-'+str(len(data['openai_calls'])),'object':'chat.completion','created':123456,'model':'gpt-4.1-mini-2025-04-14',
                'choices':[{'index':0,'message':message,'finish_reason':'stop' if answered else 'tool_calls'}],
                'usage':{'prompt_tokens':20 if answered else 10,'completion_tokens':12 if answered else 8,'total_tokens':32 if answered else 18}}
            if data.get('missing_usage') and not answered:result.pop('usage')
            return 200,result
        if path.endswith('/observations'):
            rows=[dict(v) for v in data['observations'] if 'traceId' not in query or v['traceId']==query['traceId'][0]]
            spans={s.span_id.hex():s for s in data['exports']}
            for row in rows:
                span=spans[row['id']];attrs={a.key:a.value.string_value for a in span.attributes}
                row['parentObservationId']=span.parent_span_id.hex() or None
                row['model']=attrs.get('langfuse.observation.model.name')
                row['usageDetails']=json.loads(attrs.get('langfuse.observation.usage_details','{}'))
                if row['type']=='GENERATION':row['totalCost']=0.002 if row['model']=='gpt-4.1-mini-2025-04-14' else 0.003
                if row['name']=='generate-response' and row['model']=='gpt-4.1-mini-2025-04-14' and data.get('readback_identity'):
                    key,value=data['readback_identity']
                    if key=='model':row['model']=value
                    else:row['metadata']={**row['metadata'],key:value}
                if data.get('missing_cost'):row.pop('totalCost',None)
            if data.get('missing_generation')=='application':
                rows=[r for r in rows if r['name']!='generate-response']
            elif data.get('missing_generation')=='judge':
                missing=next((r for r in rows if r['type']=='GENERATION' and r['name']!='generate-response'),None)
                if missing:rows.remove(missing)
            return 200,{'data':rows,'meta':{'cursor':None}}
        return prior(method,path,query,body)
    data['extension']=extension
