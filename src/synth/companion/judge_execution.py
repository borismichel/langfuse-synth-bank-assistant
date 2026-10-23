"""One bounded primary-provider call with an observed generation and fresh cap check."""
import asyncio
import json
import time


def call_judge(adapter,*,model,prompt,inputs,name='faithfulness-judge',check=lambda:None,on_dispatch=lambda:None,categories=None,score_values=None):
    """Expected labels never enter inputs; the rubric chooses the allowed result values."""
    if set(inputs)!={'query','context','generation'}:
        raise ValueError('Judge inputs must contain only query, context and generation')
    if not callable(getattr(adapter,'check_spending',None)):
        raise ValueError('Current operation spending checks are unavailable; no model call made')
    llm=adapter.llm();provider=llm.provider
    client=llm.bind().with_options(max_retries=0,timeout=30,
        **({'open_telemetry':False} if provider=='anthropic' else {}))
    text=prompt.compile(**inputs)
    field,reason,values,schema=judge_schema(categories,score_values)
    check()
    adapter.check_spending(provider)
    check()
    on_dispatch()
    started=time.monotonic()
    with adapter.langfuse().start_as_current_observation(as_type='generation',name=name,
            model=model,input=text,prompt=prompt,metadata={'judge_provider':provider}) as generation:
        if provider=='anthropic':
            response=client.messages.create(model=model,max_tokens=500,messages=[{'role':'user','content':text}],
                output_config={'format':{'type':'json_schema','schema':schema}})
            output=''.join(block.text for block in response.content if block.type=='text')
            complete=response.stop_reason=='end_turn'
            usage={'input':getattr(response.usage,'input_tokens',None),'output':getattr(response.usage,'output_tokens',None)}
        elif provider=='openai':
            response=client.chat.completions.create(model=model,max_tokens=500,messages=[{'role':'user','content':text}],
                response_format={'type':'json_schema','json_schema':{'name':'judge_result','strict':True,'schema':schema}})
            output=response.choices[0].message.content or ''
            complete=response.choices[0].finish_reason=='stop' and not response.choices[0].message.refusal
            usage={'input':getattr(response.usage,'prompt_tokens',None),'output':getattr(response.usage,'completion_tokens',None)}
        else:raise ValueError('Unsupported selected judge provider')
        if any(type(value) is not int or value<0 for value in usage.values()):usage=None
        generation.update(output=output,usage_details=usage,metadata={'judge_provider':provider,'provider_usage_reported':usage is not None})
    return judge_result(output,complete,usage,generation,started,categories,field,reason,values)


def judge_schema(categories,score_values):
    if (categories is None)==(score_values is None):
        raise ValueError('Exactly one judge result schema is required')
    if categories is not None:
        if not categories or any(type(v) is not str for v in categories) or len(set(categories))!=len(categories):
            raise ValueError('Judge categories must be distinct strings')
        field,reason,values='verdict','reasoning',list(categories)
        kind='string'
    else:
        if not score_values or any(v is not None and (type(v) not in (int,float) or v not in (0,0.5,1)) for v in score_values):
            raise ValueError('Judge scores must be explicit rubric values')
        field,reason,values='score','reason',list(score_values)
        kind='number'
    result_schema={'type':kind,'enum':values}
    if categories is None and None in values:
        # Anthropic rejects a mixed type-array with an enum. Keep unresolved null
        # distinct from the exact numeric rubric values using supported branches.
        result_schema={'anyOf':[{'type':'number','enum':[v for v in values if v is not None]}, {'type':'null'}]}
    schema={'type':'object','properties':{reason:{'type':'string'},field:result_schema},
            'required':[reason,field],'additionalProperties':False}
    return field,reason,values,schema


def judge_result(output,complete,usage,generation,started,categories,field,reason,values):
    try:
        def unique_fields(pairs):
            value={}
            for key,item in pairs:
                if key in value:raise ValueError('Duplicate judge output field')
                value[key]=item
            return value
        decoded=json.loads(output,object_pairs_hook=unique_fields)
    except (ValueError,TypeError):decoded=None
    # Never infer a result from prose, unknown labels, or partial JSON. bool is not a numeric score.
    valid=(complete and isinstance(decoded,dict) and set(decoded)=={reason,field} and
        type(decoded[reason]) is str and type(decoded[field]) in ((str,) if categories is not None else (int,float,type(None))) and
        decoded[field] in values)
    result={field:decoded[field] if valid else None,'output_valid':valid}
    return {**result,'text':output,'usage':usage,'generation_id':generation.id,'generation_trace_id':generation.trace_id,'latency_ms':round((time.monotonic()-started)*1000,2)}


async def call_judge_async(adapter,*,model,prompt,inputs,deadline,name='faithfulness-judge',check=lambda:None,on_dispatch=lambda:None,record=lambda result:None,categories=None,score_values=None):
    """One cancelable HTTP call on the Companion loop, with a total remaining budget."""
    if set(inputs)!={'query','context','generation'}:
        raise ValueError('Judge inputs must contain only query, context and generation')
    if not callable(getattr(adapter,'check_spending',None)):
        raise ValueError('Current operation spending checks are unavailable; no model call made')
    provider=adapter.llm().provider
    client=adapter.native_llm().judge
    text=prompt.compile(**inputs)
    field,reason,values,schema=judge_schema(categories,score_values)
    check()
    # The authorization read is synchronous; cancellation must not orphan it.
    permission=asyncio.create_task(asyncio.to_thread(adapter.check_spending,provider))
    try:
        await asyncio.shield(permission)
    except asyncio.CancelledError:
        await permission
        raise
    check()
    remaining=deadline-time.monotonic()
    if remaining<=0:raise ValueError('deadline_reached')
    on_dispatch()
    started=time.monotonic()
    with adapter.langfuse().start_as_current_observation(as_type='generation',name=name,
            model=model,input=text,prompt=prompt,metadata={'judge_provider':provider}) as generation:
        try:
            async with asyncio.timeout(max(0,min(30,deadline-time.monotonic()))):
                if provider=='anthropic':
                    response=await client.messages.create(model=model,max_tokens=500,messages=[{'role':'user','content':text}],
                        output_config={'format':{'type':'json_schema','schema':schema}})
                    output=''.join(block.text for block in response.content if block.type=='text')
                    complete=response.stop_reason=='end_turn'
                    usage={'input':getattr(response.usage,'input_tokens',None),'output':getattr(response.usage,'output_tokens',None)}
                elif provider=='openai':
                    response=await client.chat.completions.create(model=model,max_tokens=500,messages=[{'role':'user','content':text}],
                        response_format={'type':'json_schema','json_schema':{'name':'judge_result','strict':True,'schema':schema}})
                    output=response.choices[0].message.content or ''
                    complete=response.choices[0].finish_reason=='stop' and not response.choices[0].message.refusal
                    usage={'input':getattr(response.usage,'prompt_tokens',None),'output':getattr(response.usage,'completion_tokens',None)}
                else:raise ValueError('Unsupported selected judge provider')
                if any(type(value) is not int or value<0 for value in usage.values()):usage=None
                generation.update(output=output,usage_details=usage,metadata={'judge_provider':provider,'provider_usage_reported':usage is not None})
        except BaseException as exc:
            record({'output_valid':False,'verdict':None,'usage':None,
                'generation_id':generation.id,'generation_trace_id':generation.trace_id,
                'latency_ms':round((time.monotonic()-started)*1000,2),
                'error':'cancelled' if isinstance(exc,asyncio.CancelledError) else
                        'deadline_reached' if isinstance(exc,TimeoutError) and time.monotonic()>=deadline else type(exc).__name__})
            raise
    result=judge_result(output,complete,usage,generation,started,categories,field,reason,values)
    record(result)
    return result

