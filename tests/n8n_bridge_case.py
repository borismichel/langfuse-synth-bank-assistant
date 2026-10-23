"""Real provider HTTP + Langfuse SDK, without paid provider or target calls."""
import asyncio
import gzip
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from sdk_identifiers import numeric_span_ids
numeric_span_ids()

exports, model_calls, permits = [], [], []
deny_reply = '--deny-reply' in sys.argv
native_mode = 'ok'
class Target(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        data = ({'name': 'northwind-complaint-triage', 'version': 1, 'type': 'text',
                 'prompt': 'Classify the complaint as JSON.', 'labels': ['production'], 'tags': [], 'config': {}}
                if '/prompts/' in self.path else {'data': [{'id': 'fixture-project'}]})
        self.send_response(200); self.send_header('content-type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    def do_POST(self):
        raw = self.rfile.read(int(self.headers['content-length']))
        if self.headers.get('content-encoding') == 'gzip':
            raw = gzip.decompress(raw)
        if self.path.endswith('/operation-permit'):
            permits.append(json.loads(raw))
            denied = deny_reply and len(model_calls) >= 1
            data = ({'reason':'cap_exhausted'} if denied else dict(allowed=True, provider='openai',
                source='shared',checked_at='2026-10-23T12:00:00+00:00',accounting='hourly-estimate',scope='shared-company'))
            self.send_response(409 if denied else 200); self.send_header('content-type','application/json'); self.end_headers()
            self.wfile.write(json.dumps(data).encode()); return
        if self.path == '/chat/completions':
            request = json.loads(raw)
            model_calls.append(request)
            answer = (json.dumps({'category': 'fees', 'severity': 'medium', 'regulatory_escalation': False,
                                  'rationale': 'The reported duplicate charge requires review.'}) if len(model_calls) == 1
                      else 'Northwind Customer Care will review the duplicate fee. No refund is promised.')
            data = {'id': 'fixture-' + str(len(model_calls)), 'object': 'chat.completion', 'created': 123456,
                'model': request['model'], 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 12, 'total_tokens': 32}}
            self.send_response(200); self.send_header('content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(data).encode()); return
        if self.headers.get('x-langfuse-ingestion-version') == '4' and native_mode != 'ok':
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
            report = ExportTraceServiceResponse()
            report.partial_success.rejected_spans = 1
            report.partial_success.error_message = 'fixture rejected span'
            self.send_response(200 if native_mode == 'partial' else 503)
            self.send_header('content-type', 'application/x-protobuf'); self.end_headers()
            self.wfile.write(report.SerializeToString()); return
        if raw:
            batch = ExportTraceServiceRequest.FromString(raw)
            exports.extend(span for resource in batch.resource_spans for scope in resource.scope_spans for span in scope.spans)
        self.send_response(200); self.send_header('content-type', 'application/x-protobuf'); self.end_headers()

server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
thread = Thread(target=server.serve_forever, daemon=True); thread.start()
base = f'http://127.0.0.1:{server.server_port}'
os.environ.update(LANGFUSE_BASE_URL=base, LANGFUSE_PUBLIC_KEY='pk-fixture', LANGFUSE_SECRET_KEY='sk-fixture',
    OPENAI_BASE_URL=base, OPENAI_API_KEY='fixture-key', LLM_PROVIDER='openai', LLM_MODEL='gpt-4o')
instance = 'bde5ea54-d6dc-4e43-95a4-3696886c9620'
os.environ.update(PORTAL_LIVE_INSTANCE_ID=instance, DEPOT_OPERATION_TOKEN='b'*64,
    DEPOT_OPERATION_CHECK_URL=f'http://caddy:8081/api/v1/live-assets/{instance}/operation-permit')
# Only the fixed platform hostname resolves to this external HTTP fixture.
original_resolve = socket.getaddrinfo
def resolve(host, port, *args, **kwargs):
    return original_resolve('127.0.0.1', server.server_port, *args, **kwargs) if host=='caddy' and port==8081 else original_resolve(host,port,*args,**kwargs)
socket.getaddrinfo = resolve
from langfuse_synth_core.companion import CompanionAdapter
from synth.config import load_config
from northwind import config
from synth.companion.complaints import ComplaintBridge
adapter = CompanionAdapter(load_config('config/demo.yaml'), langfuse_factory=config.langfuse_factory,
    native_llm_factory=config.native_llm_factory)
config.bind_adapter(adapter)

async def main():
    global native_mode
    bridge = ComplaintBridge(adapter, instance_id='fixture-instance', base_path='/live/test/n8n')
    await bridge.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            payload = {'execution_id': '1', 'complaint': {'customer_id': 'C-1001', 'text': 'Duplicate fee; email ana@example.com card 4111 1111 1111 1111.'}}
            assert (await client.post(bridge.url + '/mask', json=payload)).status_code == 404
            headers = {'X-Northwind-Workflow': bridge.token, 'traceparent': '00-' + 'a' * 32 + '-' + 'b' * 16 + '-01'}
            masked = await client.post(bridge.url + '/mask', headers=headers, json=payload)
            assert masked.status_code == 200, masked.text
            assert 'ana@example.com' not in masked.text and '4111 1111' not in masked.text
            native = ExportTraceServiceRequest()
            resource = native.resource_spans.add()
            resource.resource.attributes.add(key='private', value={'string_value': 'ana@example.com'})
            scope = resource.scope_spans.add()
            span = scope.spans.add(name='workflow.execute', trace_id=bytes.fromhex('a' * 32),
                span_id=bytes.fromhex('c' * 16), start_time_unix_nano=1, end_time_unix_nano=2)
            span.attributes.add(key='n8n.execution.id', value={'string_value': '1'})
            span.attributes.add(key='n8n.workflow.id', value={'string_value': 'northwindComplaint'})
            span.attributes.add(key='langfuse.observation.type', value={'string_value': 'generation'})
            span.events.add(name='ana@example.com')
            native_result = await client.post(bridge.url + '/otel', headers=headers, content=native.SerializeToString())
            assert native_result.status_code == 200, native_result.text
            count = len(exports)
            repeated = await client.post(bridge.url + '/otel', headers=headers, content=native.SerializeToString())
            assert repeated.status_code == 200 and len(exports) == count
            span.trace_id = bytes.fromhex('f' * 32)
            assert (await client.post(bridge.url + '/otel', headers=headers, content=native.SerializeToString())).status_code == 400
            classified = await client.post(bridge.url + '/classify', headers=headers, json=masked.json())
            assert classified.status_code == 200, classified.text
            repeat = await client.post(bridge.url + '/classify', headers=headers, json=masked.json())
            assert repeat.json() == classified.json() and len(model_calls) == 1
            reply = await client.post(bridge.url + '/reply', headers=headers, json=classified.json())
            if deny_reply:
                assert reply.status_code == 502, reply.text
                assert len(model_calls) == 1, 'Denied reply must never reach the model provider'
                assert len(permits) == 2 and all(p == {'provider':'openai'} for p in permits)
                again = await client.post(bridge.url + '/reply', headers=headers, json=classified.json())
                assert again.status_code == 409 and len(model_calls) == 1 and len(permits) == 2
                cached = await client.post(bridge.url + '/classify', headers=headers, json=masked.json())
                assert cached.json() == classified.json() and len(model_calls) == 1 and len(permits) == 2
                assert 'cap_exhausted' not in reply.text and 'bbbbbbbb' not in reply.text
                return
            assert reply.status_code == 200 and reply.json()['category'] == 'fees', reply.text
            assert reply.json()['trace_id'] == 'a' * 32
            assert reply.json()['trace_url'].startswith(base + '/project/fixture-project/traces/')
            assert len(model_calls) == 2
            roots = [item for item in exports if item.name == 'n8n-complaint-triage']
            assert len(roots) == 1
            root = roots[0]
            assert root.span_id.hex().isdecimal()
            attributes = {item.key: item.value.string_value for item in root.attributes}
            output = json.loads(attributes['langfuse.observation.output'])
            assert output['observation_id'] == reply.json()['observation_id'] == root.span_id.hex(), output['observation_id']
            # Failures remain visible, and OTLP partial-success is returned unchanged.
            assert (await client.post(bridge.url + '/otel', headers=headers, content=b'x' * (1024 * 1024 + 1))).status_code == 413
            span.trace_id = bytes.fromhex('a' * 32)
            span.span_id = bytes.fromhex('d' * 16)
            native_mode = 'partial'
            partial = await client.post(bridge.url + '/otel', headers=headers, content=native.SerializeToString())
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
            assert partial.status_code == 200
            assert ExportTraceServiceResponse.FromString(partial.content).partial_success.rejected_spans == 1
            assert bridge.error == 'NativeTraceExportRejected'
            native_mode = 'failure'
            span.span_id = bytes.fromhex('e' * 16)
            failed = await client.post(bridge.url + '/otel', headers=headers, content=native.SerializeToString())
            assert failed.status_code == 503 and bridge.error == 'NativeTraceExportFailed'
            native_mode = 'ok'
    finally:
        await bridge.stop()
        config.shutdown()

try:
    asyncio.run(main())
finally:
    server.shutdown(); thread.join(); server.server_close()
assert len(model_calls) == (1 if deny_reply else 2)
assert len(permits) == 2
assert 'ana@example.com' not in json.dumps(model_calls) and '4111 1111' not in json.dumps(model_calls)
attrs = lambda span: {a.key: a.value.string_value for a in span.attributes}
generations = [s for s in exports if attrs(s).get('langfuse.observation.type') == 'generation']
assert len(generations) == (1 if deny_reply else 2), [s.name for s in exports]
assert all(s.trace_id.hex() == 'a' * 32 for s in exports)
assert ({'n8n-complaint-triage', 'mask-pii', 'classify-complaint'} | (set() if deny_reply else {'draft-reply'})) <= {s.name for s in exports}
assert all(s.name != 'northwind-assistant' for s in exports)
assert 'ana@example.com' not in str(exports) and '4111 1111' not in str(exports)
print(json.dumps({'masked_before_model': True, 'model_calls': len(model_calls), 'spending_checks':len(permits), 'reply_denied':deny_reply, 'joined_native_trace': True, 'idempotent_steps': True, 'bounded_export': True, 'partial_success_and_failure': True}))
