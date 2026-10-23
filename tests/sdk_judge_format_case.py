"""Categorical judge results through real provider SDK and native OTLP boundaries."""
import gzip
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from operation_fixture import configure, respond
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

provider = os.environ['BANK_TEST_PROVIDER']
calls, permits, exports = [], [], []
reply = {'text': '', 'finish': None, 'refusal': None}
categories = ['pass', 'fail', 'minor', 'unknown']
score_values = None

class API(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def send_json(self, payload):
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
    def do_GET(self):
        from northwind.compliance import POLICIES
        self.send_json({'name':'fixture-policy', 'version':1, 'type':'text',
                        'prompt':POLICIES[2].prompt, 'labels':[], 'tags':[], 'config':{}})
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path.endswith('/operation-permit'):
            permits.append(json.loads(raw)); respond(self, raw); return
        if self.headers.get('Content-Encoding') == 'gzip': raw = gzip.decompress(raw)
        if self.path.endswith('/otel/v1/traces'):
            batch = ExportTraceServiceRequest.FromString(raw)
            exports.extend(span for resource in batch.resource_spans for scope in resource.scope_spans for span in scope.spans)
            self.send_json({}); return
        request = json.loads(raw); calls.append(request)
        assert request['max_tokens'] == 500
        assert request['messages'] == [{'role':'user', 'content':compiled}]
        if provider == 'anthropic':
            schema = request['output_config']['format']['schema']
            assert request['output_config']['format']['type'] == 'json_schema'
        else:
            fmt = request['response_format']
            assert fmt['type'] == 'json_schema' and fmt['json_schema']['strict'] is True
            schema = fmt['json_schema']['schema']
        if score_values is None:
            expected={'type':'object','properties':{'reasoning':{'type':'string'},
                'verdict':{'type':'string','enum':categories}},
                'required':['reasoning','verdict'],'additionalProperties':False}
        else:
            expected={'type':'object','properties':{'reason':{'type':'string'},
                'score':{'type':['number','null'] if None in score_values else 'number','enum':score_values}},
                'required':['reason','score'],'additionalProperties':False}
        assert schema == expected
        if provider == 'anthropic':
            self.send_json({'id':'message', 'type':'message', 'role':'assistant', 'model':'fixture-judge',
                'content':[{'type':'text','text':reply['text']}], 'stop_reason':reply['finish'] or 'end_turn',
                'stop_sequence':None, 'usage':{'input_tokens':31,'output_tokens':17}})
        else:
            self.send_json({'id':'chat', 'object':'chat.completion', 'created':123456, 'model':'fixture-judge',
                'choices':[{'index':0, 'message':{'role':'assistant','content':reply['text'],'refusal':reply['refusal']},
                            'finish_reason':reply['finish'] or 'stop'}],
                'usage':{'prompt_tokens':31,'completion_tokens':17,'total_tokens':48}})

server = ThreadingHTTPServer(('127.0.0.1', 0), API)
thread = Thread(target=server.serve_forever, daemon=True); thread.start()
base = f'http://127.0.0.1:{server.server_port}'
os.environ.update(LANGFUSE_BASE_URL=base, LANGFUSE_PUBLIC_KEY='pk-format', LANGFUSE_SECRET_KEY='sk-format',
                  ANTHROPIC_BASE_URL=base, OPENAI_BASE_URL=base, ANTHROPIC_API_KEY='fixture',
                  OPENAI_API_KEY='fixture', LLM_PROVIDER=provider, LLM_MODEL='fixture-judge')
configure(server.server_port)
from langfuse_synth_core.companion import CompanionAdapter
from synth.config import load_config
from synth.companion.app import REQUIRES_SECRETS
from synth.companion.judge_execution import call_judge
from northwind import config

adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
    langfuse_factory=config.langfuse_factory, native_llm_factory=config.native_llm_factory)
client = adapter.langfuse()
prompt = client.get_prompt('fixture-policy', version=1, cache_ttl_seconds=0)
inputs = {'query':'How much is the Premier monthly fee?', 'context':'APPROVED POLICY: USD 25.',
          'generation':'The monthly fee is USD 25.'}
compiled = prompt.compile(**inputs)
try:
    for label in categories:
        reply['text'] = json.dumps({'reasoning':'Only the supplied policy and answer were evaluated.', 'verdict':label})
        result = call_judge(adapter, model='fixture-judge', prompt=prompt, inputs=inputs, categories=categories)
        assert result['output_valid'] and result['verdict'] == label, result
        assert result['usage'] == {'input':31,'output':17}
    malformed = [
        '**Evidence:** The answer cites the approved policy.\n\n**Verdict: pass**',
        '{"reasoning":"Supported", "verdict":"pass", "verdict":"fail"}',
        '{"reasoning":"Supported", "verdict":"pass", "extra":true}',
        '{"reasoning":"Supported", "verdict":true}',
        '{"reasoning":[], "verdict":"pass"}',
        '{"reasoning":"Supported", "verdict":"PASS"}',
        '{"reasoning":"Supported", "verdict":"invented"}',
        '{"reasoning":"Supported", "verdict":',
    ]
    for text in malformed:
        reply['text'] = text
        result = call_judge(adapter, model='fixture-judge', prompt=prompt, inputs=inputs, categories=categories)
        assert result['output_valid'] is False and result['verdict'] is None, result
        assert result['text'] == text and result['usage'] == {'input':31,'output':17}
    reply['text'] = '{"reasoning":"Supported", "verdict":"pass"}'
    reply['finish'] = 'max_tokens' if provider == 'anthropic' else 'length'
    result = call_judge(adapter, model='fixture-judge', prompt=prompt, inputs=inputs, categories=categories)
    assert result['output_valid'] is False and result['verdict'] is None, 'Truncated output must not count even if parseable'
    if provider == 'openai':
        reply.update(finish=None, refusal='Cannot provide a verdict')
        result = call_judge(adapter, model='fixture-judge', prompt=prompt, inputs=inputs, categories=categories)
        assert result['output_valid'] is False and result['verdict'] is None
    reply.update(finish=None, refusal=None)
    for score_values in ([0,0.5,1],[0,0.5,1,None]):
        for value in score_values:
            reply['text']=json.dumps({'reason':'Supplied evidence.', 'score':value})
            result=call_judge(adapter,model='fixture-judge',prompt=prompt,inputs=inputs,score_values=score_values)
            assert result['output_valid'] is True and result['score']==value, result
        for text in ('{"reason":"Evidence", "score":true}', '{"reason":"Evidence", "score":"1"}',
                     '{"reason":"Evidence", "score":0.7}', '{"reason":"Evidence", "score":NaN}',
                     '{"reason":"Evidence", "score":1,"score":0}', '{"score":1}',
                     '**Evidence:** Supported.\n\n**Score: 1**'):
            reply['text']=text
            result=call_judge(adapter,model='fixture-judge',prompt=prompt,inputs=inputs,score_values=score_values)
            assert result['output_valid'] is False and result['score'] is None, result
        reply['text']='{"reason":"Evidence", "score":null}'
        result=call_judge(adapter,model='fixture-judge',prompt=prompt,inputs=inputs,score_values=score_values)
        assert result['output_valid'] is (None in score_values) and result['score'] is None
    client.flush()
    assert len(permits) == len(calls) == len(exports), (len(permits),len(calls),len(exports))
    assert all(span.name == 'faithfulness-judge' for span in exports)
finally:
    client.shutdown(); server.shutdown(); server.server_close(); thread.join(timeout=2)
print(f'{provider}: strict schema, categorical outcomes, invalid/refused/truncated output and one permit/export per call verified')
