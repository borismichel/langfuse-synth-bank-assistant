"""Real provider HTTP cancellation, including a response that never becomes idle."""
import asyncio
import gzip
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from operation_fixture import configure, respond
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

provider=os.environ['BANK_TEST_PROVIDER']
calls=[];exports=[];permits=[];disconnected=Event()
reply={'mode':'valid','text':'{"reasoning":"Evidence only", "verdict":"pass"}'}
class API(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def send_json(self,value):
        data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)))
        self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
    def do_GET(self):
        self.send_json({'name':'fixture-policy','version':1,'type':'text','prompt':'Query {{query}}. Evidence {{context}}. Answer {{generation}}.', 'labels':[],'tags':[],'config':{}})
    def do_POST(self):
        raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
        if self.path.endswith('/operation-permit'):permits.append(json.loads(raw));respond(self,raw);return
        if self.headers.get('Content-Encoding')=='gzip':raw=gzip.decompress(raw)
        if self.path.endswith('/otel/v1/traces'):
            batch=ExportTraceServiceRequest.FromString(raw)
            exports.extend(s for r in batch.resource_spans for scope in r.scope_spans for s in scope.spans)
            self.send_json({});return
        request=json.loads(raw);calls.append(request)
        assert request['model']=='selected-judge' and request['max_tokens']==500
        assert request['messages']==[{'role':'user','content':compiled}]
        fmt=request['output_config']['format'] if provider=='anthropic' else request['response_format']
        schema=fmt['schema'] if provider=='anthropic' else fmt['json_schema']['schema']
        assert schema=={'type':'object','properties':{'reasoning':{'type':'string'},'verdict':{'type':'string','enum':['pass','fail']}},'required':['reasoning','verdict'],'additionalProperties':False}
        if reply['mode']=='drip':
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length','100000');self.end_headers()
            try:
                for _ in range(250):self.wfile.write(b' ');self.wfile.flush();time.sleep(.02)
            except (BrokenPipeError,ConnectionResetError):disconnected.set()
            return
        if provider=='anthropic':
            self.send_json({'id':'message','type':'message','role':'assistant','model':'selected-judge','content':[{'type':'text','text':reply['text']}],'stop_reason':'end_turn','usage':{'input_tokens':31,'output_tokens':17}})
        else:
            self.send_json({'id':'chat','object':'chat.completion','created':123,'model':'selected-judge','choices':[{'index':0,'message':{'role':'assistant','content':reply['text']},'finish_reason':'stop'}],'usage':{'prompt_tokens':31,'completion_tokens':17,'total_tokens':48}})
server=ThreadingHTTPServer(('127.0.0.1',0),API);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
base=f'http://127.0.0.1:{server.server_port}'
os.environ.update(LANGFUSE_BASE_URL=base,LANGFUSE_PUBLIC_KEY='pk-async-fixture',LANGFUSE_SECRET_KEY='sk-async-fixture',ANTHROPIC_BASE_URL=base,OPENAI_BASE_URL=base,ANTHROPIC_API_KEY='fixture',OPENAI_API_KEY='fixture',LLM_PROVIDER=provider,LLM_MODEL='application-model')
configure(server.server_port)
from langfuse_synth_core.companion import CompanionAdapter
from synth.config import load_config
from synth.companion.app import REQUIRES_SECRETS
from synth.companion.judge_execution import call_judge_async
from northwind import config
adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,langfuse_factory=config.langfuse_factory,native_llm_factory=config.native_llm_factory)
client=adapter.langfuse();prompt=client.get_prompt('fixture-policy',version=1,cache_ttl_seconds=0)
inputs={'query':'Question','context':'Approved policy','generation':'Delivered answer'};compiled=prompt.compile(**inputs)
async def main():
    async def judge(seconds=5):
        return await call_judge_async(adapter,model='selected-judge',prompt=prompt,inputs=inputs,categories=['pass','fail'],deadline=time.monotonic()+seconds)
    try:
        result=await judge();assert result['output_valid'] and result['verdict']=='pass' and result['usage']=={'input':31,'output':17}
        reply['text']='{"reasoning":"Evidence", "verdict":"pass", "verdict":"fail"}'
        result=await judge();assert not result['output_valid'] and result['verdict'] is None and result['text']==reply['text']
        reply['mode']='drip'
        try:await judge(.3)
        except TimeoutError:pass
        else:raise AssertionError('Dripping response exceeded total request budget')
        until=time.monotonic()+1
        while not disconnected.is_set() and time.monotonic()<until:await asyncio.sleep(.01)
        assert disconnected.is_set(),'Timeout returned while provider connection was still receiving bytes'
        disconnected.clear()
        pending=asyncio.create_task(judge())
        until=time.monotonic()+2
        while len(calls)<4 and time.monotonic()<until:await asyncio.sleep(.01)
        assert len(calls)==4
        pending.cancel()
        try:await pending
        except asyncio.CancelledError:pass
        else:raise AssertionError('Explicit cancellation was swallowed')
        until=time.monotonic()+1
        while not disconnected.is_set() and time.monotonic()<until:await asyncio.sleep(.01)
        assert disconnected.is_set(),'Cancelled request still owns a receiving socket'
        assert len(calls)==len(permits)==4,'No retry or extra paid request' 
    finally:
        await config.close_native_clients()
    assert adapter.native_llm().judge.is_closed,'Owned judge client must close before completion'
try:
    asyncio.run(main());client.flush()
    generations=[s for s in exports if s.name=='faithfulness-judge']
    assert len(generations)==4,'Exactly one generation per admitted request, including deadline failure'
    for span in generations:
        attrs={a.key:a.value for a in span.attributes}
        assert attrs['langfuse.observation.prompt.name'].string_value=='fixture-policy'
finally:
    client.shutdown();server.shutdown();server.server_close();thread.join(timeout=2)
print(provider+': selected model, native strict schema, prompt association and actual dripping HTTP cancellation passed')
