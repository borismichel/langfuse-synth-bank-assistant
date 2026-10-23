"""Isolated real SDK/LangGraph contract driven only through delivered HTTP actions."""
import gzip
import json
import os
import signal
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

exports = []
model_calls = []
fail_after_action = False

class API(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if '/prompts/' in self.path:
            from northwind.prompts import V5_RELEASE
            payload = {'name': 'northwind-assistant-system', 'version': 1, 'prompt': V5_RELEASE,
                       'labels': ['production'], 'tags': [], 'config': {}, 'type': 'text'}
        else:
            payload = {'data': [{'id': 'fixture-project', 'name': 'bank-demo'}]}
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        from operation_fixture import respond
        if respond(self,body): return
        if self.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
        if urlparse(self.path).path.endswith('/otel/v1/traces') and body:
            if "json" in self.headers.get("Content-Type", ""):
                from google.protobuf.json_format import Parse
                batch = Parse(body.decode(), ExportTraceServiceRequest())
            else:
                batch = ExportTraceServiceRequest.FromString(body)
            exports.extend(span for resource in batch.resource_spans for scope in resource.scope_spans for span in scope.spans)
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(b'{"successes":[],"errors":[]}')

class ScriptedProvider(BaseChatModel):
    model: str = 'fixture-native-model'
    @property
    def _llm_type(self):
        return self.model
    def bind_tools(self, tools, **kwargs):
        assert 'list_accounts' in [tool.name for tool in tools]
        return self
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        model_calls.append(1)
        if 'The customer explicitly confirmed' in str(messages[0].content):
            if fail_after_action:
                raise RuntimeError('simulated provider failure after tool committed')
            answer = AIMessage(content='Banking action completed.' if 'Confirmo' not in str(messages[-1].content) else 'La acción bancaria se ha completado.')
        elif isinstance(messages[-1], ToolMessage):
            payload = json.loads(messages[-1].content)
            # Deliberately wrong model prose must not claim an unconfirmed mutation.
            answer = AIMessage(content='Your card was blocked.' if payload.get('status') == 'confirmation_required' else messages[-1].content)
        else:
            question = next(str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage))
            action = ('open_dispute' if 'disputa' in question.lower() else 'block_card' if 'block' in question.lower() or 'bloquee' in question.lower() else 'list_accounts')
            args = ({'card_last4': '4417', 'reason': 'lost'} if action == 'block_card' else {'transaction_id': 'TX-88101', 'reason': 'No reconozco este cargo'} if action == 'open_dispute' else {})
            answer = AIMessage(content='', tool_calls=[{'name': action, 'args': args, 'id': 'banking', 'type': 'tool_call'}])
        answer.usage_metadata = {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}
        return ChatResult(generations=[ChatGeneration(message=answer)], llm_output={'model_name': self.model})

server = ThreadingHTTPServer(('127.0.0.1', 0), API)
thread = Thread(target=server.serve_forever, daemon=True); thread.start()
base = f'http://127.0.0.1:{server.server_port}'
os.environ.update(LANGFUSE_BASE_URL=base, LANGFUSE_PUBLIC_KEY='pk-fixture', LANGFUSE_SECRET_KEY='sk-fixture', ANTHROPIC_API_KEY='fixture-key', LLM_PROVIDER='anthropic', PORTAL_LIVE_INSTANCE_ID='fixture-deployment')
from operation_fixture import configure
configure(server.server_port)
from langfuse_synth_core.companion import CompanionAdapter
from synth.config import load_config
from synth.companion.app import create_app, REQUIRES_SECRETS
from northwind import config
adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
    health_path='/api/health', langfuse_factory=config.langfuse_factory,
    native_llm_factory=lambda **_: ScriptedProvider())
try:
    with TestClient(create_app(adapter)) as browser:
        health = browser.get('/api/health').json()
        assert health['ready'] and health['banking_ready'], health
        assert not model_calls
        session = browser.post('/api/session').json()['session_id']
        response = browser.post('/api/chat', json={'session_id': session, 'message': 'List my accounts', 'request_id': 'request-accounts'})
        assert response.status_code == 200, response.text
        assert 'ACC-1001-01' in response.json()['answer'], response.text
        proposal = browser.post('/api/chat', json={'session_id': session, 'message': 'Block my card 4417', 'request_id': 'propose-block'}).json()
        pending = proposal['banking_actions'][0]
        assert pending['status'] == 'confirmation_required', proposal
        assert 'Confirmation required' in proposal['answer'] and 'Your card was blocked' not in proposal['answer']
        before = browser.post('/api/chat', json={'session_id': session, 'message': 'List my accounts', 'request_id': 'before-confirm'}).json()
        assert '\"status\": \"active\"' in before['answer'], before
        confirmed = browser.post('/api/confirm', json={'session_id': session, 'action_id': pending['action_id'], 'request_id': 'confirm-block', 'lang': 'en'})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()['banking_actions'][0]['status'] == 'blocked', confirmed.text
        count_before_retry = len(model_calls)
        retry = browser.post('/api/confirm', json={'session_id': session, 'action_id': pending['action_id'], 'request_id': 'confirm-block', 'lang': 'en'})
        assert retry.json() == confirmed.json()
        assert len(model_calls) == count_before_retry
        conflict = browser.post('/api/confirm', json={'session_id': session, 'action_id': pending['action_id'], 'request_id': 'confirm-block', 'lang': 'es'})
        assert conflict.status_code == 409
        # Spanish dispute: the tool commits, then the provider response fails.
        proposal = browser.post('/api/chat', json={'session_id': session, 'message': 'Abra una disputa por TX-88101', 'request_id': 'propose-dispute'}).json()
        dispute = proposal['banking_actions'][0]
        assert dispute['status'] == 'confirmation_required'
        fail_after_action = True
        failed_answer = browser.post('/api/confirm', json={'session_id': session, 'action_id': dispute['action_id'], 'request_id': 'confirm-dispute', 'lang': 'es'})
        assert failed_answer.status_code == 200, failed_answer.text
        receipt = failed_answer.json()['banking_actions'][0]
        assert receipt['status'] == 'opened' and receipt['case_id'].startswith('DSP-')
        count_before_retry = len(model_calls)
        retry = browser.post('/api/confirm', json={'session_id': session, 'action_id': dispute['action_id'], 'request_id': 'confirm-dispute', 'lang': 'es'})
        assert retry.json() == failed_answer.json()
        assert len(model_calls) == count_before_retry
        fail_after_action = False
        # A second browser selecting the same fictional customer owns independent state.
        first_cookie = browser.cookies.get('northwind_session')
        browser.cookies.clear()
        other_session = browser.post('/api/session').json()['session_id']
        cross = browser.post('/api/confirm', json={'session_id': other_session, 'action_id': pending['action_id'], 'request_id': 'cross-confirm'})
        assert cross.status_code == 409
        forged = browser.post('/api/chat', json={'session_id': session, 'message': 'List my accounts', 'request_id': 'forge'})
        assert forged.status_code == 409
        other = browser.post('/api/chat', json={'session_id': other_session, 'message': 'List my accounts', 'request_id': 'other-accounts'}).json()
        assert '\"status\": \"active\"' in other['answer'], other
        selected = browser.post('/api/session', json={'customer_id': 'C-1002'})
        assert selected.status_code == 200, selected.text
        selected_id = selected.json()['session_id']
        wrong = browser.post('/api/chat', json={'session_id': selected_id, 'customer_id': 'C-1001', 'message': 'List my accounts'})
        assert wrong.status_code == 403
        ben = browser.post('/api/chat', json={'session_id': selected_id, 'customer_id': 'C-1002', 'message': 'List my accounts'}).json()
        assert 'ACC-1002-01' in ben['answer'] and 'ACC-1001-01' not in ben['answer']
        # Controlled process failure: kill only this test process's exact MCP child.
        children = []
        for line in subprocess.check_output(['ps', '-axo', 'pid=,ppid=,command='], text=True).splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) == 3 and int(parts[1]) == os.getpid() and 'synth.companion.mcp_runtime' in parts[2]:
                children.append(int(parts[0]))
        assert len(children) == 1, children
        os.kill(children[0], signal.SIGTERM)
        before_health = len(model_calls)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            health = browser.get('/api/health')
            if not health.json()['banking_ready']:
                break
            time.sleep(.05)
        assert health.status_code == 200 and not health.json()['ready'], health.text
        assert browser.get('/').status_code == 200
        assert len(model_calls) == before_health, 'health ran inference'
        unavailable = browser.post('/api/chat', json={'session_id': selected_id, 'customer_id': 'C-1002', 'message': 'List my accounts'})
        assert unavailable.status_code == 503
    try:
        os.kill(children[0], 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError('owned MCP child survived Companion teardown')
finally:
    server.shutdown(); thread.join(); server.server_close()
assert any(span.name == 'mcp-server: list_accounts' for span in exports), [span.name for span in exports]
def attrs(span):
    return {a.key: a.value.string_value for a in span.attributes}
roots = [span for span in exports if span.name == 'northwind-assistant']
span_by_id = {span.span_id: span for span in exports}
assert len(span_by_id) == len(exports), 'duplicate exported spans'
for span in exports:
    if span not in roots:
        assert span.parent_span_id in span_by_id, ('missing parent', span.name, span.parent_span_id.hex())
for span in [s for s in exports if s.name.startswith('mcp-server:')]:
    parent = span_by_id[span.parent_span_id]
    assert parent.name.startswith('mcp-client:'), parent.name
    assert parent.trace_id == span.trace_id
    for key in ('session.id', 'user.id', 'langfuse.version', 'langfuse.environment',
                'langfuse.trace.metadata.live_instance_id', 'langfuse.trace.metadata.live_run_id'):
        assert attrs(span).get(key), (span.name, key, attrs(span))
        assert attrs(span)[key] == attrs(parent)[key], (key, attrs(span), attrs(parent))
generations = [s for s in exports if attrs(s).get('langfuse.observation.type') == 'generation']
assert len(generations) == len(model_calls), (len(generations), len(model_calls))
assert first_cookie not in json.dumps([attrs(s) for s in exports])
assert not any('northwind_binding' in str(attrs(s)) for s in exports)
assert all(attrs(s).get('langfuse.observation.model.name') == 'fixture-native-model' for s in generations)
assert len(model_calls) >= 6
print(json.dumps({'mcp_read': True}))
