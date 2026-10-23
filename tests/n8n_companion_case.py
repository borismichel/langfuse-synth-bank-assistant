"""Real provider HTTP + Langfuse SDK, without paid provider or target calls."""
import asyncio
import gzip
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

exports, model_calls, dispatch_events = [], [], []
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
        from operation_fixture import respond
        if self.path.endswith('/operation-permit'):
            dispatch_events.append('permit')
        if respond(self, raw):
            return
        if self.path == '/chat/completions':
            dispatch_events.append('model')
            request = json.loads(raw)
            model_calls.append(request)
            answer = (json.dumps({'category': 'fees', 'severity': 'medium', 'regulatory_escalation': False,
                                  'rationale': 'The reported duplicate charge requires review.'}) if len(model_calls) == 1
                      else 'Northwind Customer Care will review the duplicate fee. No refund is promised.')
            if len(model_calls) == 1 and request.get('response_format', {}).get('type') != 'json_schema':
                answer = '```json\n' + answer + '\n```'
            data = {'id': 'fixture-' + str(len(model_calls)), 'object': 'chat.completion', 'created': 123456,
                'model': request['model'], 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 12, 'total_tokens': 32}}
            self.send_response(200); self.send_header('content-type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(data).encode()); return
        if raw:
            batch = ExportTraceServiceRequest.FromString(raw)
            exports.extend(span for resource in batch.resource_spans for scope in resource.scope_spans for span in scope.spans)
        self.send_response(200); self.send_header('content-type', 'application/x-protobuf'); self.end_headers()

server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
thread = Thread(target=server.serve_forever, daemon=True); thread.start()
base = f'http://127.0.0.1:{server.server_port}'
from operation_fixture import configure
configure(server.server_port)
os.environ.update(LANGFUSE_BASE_URL=base, LANGFUSE_PUBLIC_KEY='pk-fixture', LANGFUSE_SECRET_KEY='sk-fixture',
    OPENAI_BASE_URL=base, OPENAI_API_KEY='fixture-key', LLM_PROVIDER='openai', LLM_MODEL='gpt-4o')
from langfuse_synth_core.companion import CompanionAdapter
from synth.config import load_config
from northwind import config
from synth.companion.complaints import ComplaintBridge
adapter = CompanionAdapter(load_config('config/demo.yaml'), langfuse_factory=config.langfuse_factory,
    native_llm_factory=config.native_llm_factory)
config.bind_adapter(adapter)


from fastapi import FastAPI
from synth.companion.workflows import WorkflowService, mount_workflow
from langfuse_synth_core.companion.access import guarded_app
import socket
import uvicorn

async def delivered():
    proof = 'a' * 64
    os.environ.update(PORTAL_LIVE_PROOF=proof,
        PORTAL_LIVE_PUBLIC_ORIGIN='http://localhost:3009', LIVE_BASE_PATH='/live/proof',
        PORTAL_LIVE_PROTECTED_PATHS=json.dumps([{'path': '/n8n', 'access': 'presenter'}]))
    service = WorkflowService(adapter, enabled=True)
    await service.start()
    assert service.ready, service.detail()
    app = FastAPI()
    mount_workflow(app, service)
    sock = socket.socket(); sock.bind(('127.0.0.1', 0))
    local = f'http://127.0.0.1:{sock.getsockname()[1]}'
    gate = guarded_app(app, health_path='/api/health')
    web = uvicorn.Server(uvicorn.Config(gate, log_level='error', access_log=False))
    task = asyncio.create_task(web.serve(sockets=[sock]))
    while not web.started:
        await asyncio.sleep(.01)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=40) as client:
            assert (await client.get(local + '/n8n/rest/login')).status_code == 404
            headers = {'x-depot-live-proof': proof}
            login = await client.get(local + '/n8n/rest/login', headers=headers)
            assert login.status_code == 200 and 'set-cookie' not in login.headers, login.text
            page = await client.get(local + '/n8n/workflow/northwindComplaint', headers=headers)
            assert page.status_code == 200 and '/live/proof/n8n/' in page.text
            import re
            asset = re.search(r'src="(/live/proof/n8n/assets/[^" ]+\.js)"', page.text)
            assert asset, page.text[:2000]
            js = await client.get(local + asset[1].removeprefix('/live/proof'), headers=headers)
            assert js.status_code == 200 and len(js.content) > 1000
            response = await client.post(local + '/n8n/webhook/complaint-triage', headers=headers,
                json={'text': 'Duplicate fee; email ana@example.com card 4111 1111 1111 1111.', 'customer_id': 'C-1001'}, timeout=60)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result['category'] == 'fees' and len(model_calls) == 2, result
            assert dispatch_events == ['permit', 'model', 'permit', 'model'], dispatch_events
            assert result['n8n_execution_url'].startswith('/live/proof/n8n/')
            assert result['trace_url'].startswith(base + '/project/fixture-project/traces/')
            async with client.stream('GET', local + '/n8n/rest/push?pushRef=fixture', headers=headers) as stream:
                assert stream.status_code == 200, stream.status_code
                started = asyncio.get_running_loop().time()
                pings = 0
                async for line in stream.aiter_lines():
                    if 'keepalive' in line:
                        pings += 1
                        if pings == 3:
                            break
                assert asyncio.get_running_loop().time() - started > 30
            assert service.ready, service.detail()
            trace = bytes.fromhex(result['trace_id'])
            related = [s for s in exports if s.trace_id == trace]
            assert {'workflow.execute', 'node.execute', 'n8n-complaint-triage', 'classify-complaint', 'draft-reply'} <= {s.name for s in related}, [s.name for s in related]
            classifications = [s for s in related if s.name == 'classify-complaint']
            assert len(classifications) == 1
            attrs = {a.key: a.value for a in classifications[0].attributes}
            assert attrs['langfuse.observation.prompt.name'].string_value == 'northwind-complaint-triage'
            assert attrs['langfuse.observation.prompt.version'].int_value == 1
            assert (await client.post(local + '/n8n/webhook/complaint-triage', headers={**headers, 'origin': 'https://foreign.invalid'}, json={})).status_code == 403
            assert (await client.post(local + '/n8n/rest/owner/setup', headers=headers, json={})).status_code == 404
    finally:
        web.should_exit = True
        await task
        await service.stop()
        config.shutdown()
    assert service.runtime.process.returncode is not None and not service.runtime.ready
    assert not __import__('pathlib').Path(service.runtime.directory.name).exists()
    assert len(model_calls) == 2
    assert 'ana@example.com' not in str(exports) and '4111 1111' not in str(exports)
    print(json.dumps({'delivered_editor': True, 'asset': True, 'private_access': True,
        'native_and_generations_same_trace': True, 'sse_over_30s': True, 'cleanup': True,
        'fresh_permission_per_model_call': True}))

try:
    asyncio.run(delivered())
finally:
    server.shutdown(); thread.join(); server.server_close()
