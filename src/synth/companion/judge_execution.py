"""One bounded primary-provider call with an observed generation and fresh cap check."""
import time


def call_judge(adapter,*,model,prompt,inputs,name='faithfulness-judge',check=lambda:None,on_dispatch=lambda:None):
    """Expected labels must never be included in inputs; parsing belongs to the rubric."""
    if set(inputs)!={'query','context','generation'}:
        raise ValueError('Judge inputs must contain only query, context and generation')
    if not callable(getattr(adapter,'check_spending',None)):
        raise ValueError('Current operation spending checks are unavailable; no model call made')
    llm=adapter.llm();provider=llm.provider
    client=llm.bind().with_options(max_retries=0,timeout=30,
        **({'open_telemetry':False} if provider=='anthropic' else {}))
    text=prompt.compile(**inputs)
    check()
    adapter.check_spending(provider)
    check()
    on_dispatch()
    started=time.monotonic()
    with adapter.langfuse().start_as_current_observation(as_type='generation',name=name,
            model=model,input=text,prompt=prompt,metadata={'judge_provider':provider}) as generation:
        if provider=='anthropic':
            response=client.messages.create(model=model,max_tokens=500,messages=[{'role':'user','content':text}])
            output=''.join(block.text for block in response.content if block.type=='text')
            usage={'input':getattr(response.usage,'input_tokens',None),'output':getattr(response.usage,'output_tokens',None)}
        elif provider=='openai':
            response=client.chat.completions.create(model=model,max_tokens=500,messages=[{'role':'user','content':text}])
            output=response.choices[0].message.content or ''
            usage={'input':getattr(response.usage,'prompt_tokens',None),'output':getattr(response.usage,'completion_tokens',None)}
        else:raise ValueError('Unsupported selected judge provider')
        if any(type(value) is not int or value<0 for value in usage.values()):usage=None
        generation.update(output=output,usage_details=usage,metadata={'judge_provider':provider,'provider_usage_reported':usage is not None})
    return {'text':output,'usage':usage,'observation_id':generation.id,'trace_id':generation.trace_id,'latency_ms':round((time.monotonic()-started)*1000,2)}
