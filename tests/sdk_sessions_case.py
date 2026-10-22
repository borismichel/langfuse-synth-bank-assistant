"""Isolated real SDK/LangGraph contract driven only through delivered HTTP actions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import gzip
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

exports = []
score_writes = []
saved_scores = {}
hide_feedback = False
reject_feedback = False
model_calls = []
fail_after_action = False
provider_waiting = Event()
provider_release = Event()

class API(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if '/v3/scores' in self.path:
            from urllib.parse import parse_qs
            wanted = parse_qs(urlparse(self.path).query).get('id', [''])[0]
            payload = {'data': [saved_scores[wanted]] if wanted in saved_scores and not hide_feedback else [], 'meta': {'limit': 1, 'cursor': None}}
        elif '/prompts/' in self.path:
            from northwind.prompts import V5_RELEASE
            payload = {'name': 'northwind-assistant-system', 'version': 1, 'prompt': V5_RELEASE,
                       'labels': ['production'], 'tags': [], 'config': {}, 'type': 'text'}
        else:
            payload = {'data': [{'id': 'fixture-project', 'name': 'bank-demo'}]}
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        if self.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
        if urlparse(self.path).path.endswith('/otel/v1/traces') and body:
            if "json" in self.headers.get("Content-Type", ""):
                from google.protobuf.json_format import Parse
                batch = Parse(body.decode(), ExportTraceServiceRequest())
            else:
                batch = ExportTraceServiceRequest.FromString(body)
            exports.extend(span for resource in batch.resource_spans for scope in resource.scope_spans for span in scope.spans)
        if urlparse(self.path).path.endswith('/public/scores'):
            batch = json.loads(body)
            assert isinstance(batch, list)
            score_writes.extend(batch)
            if reject_feedback:
                self.send_response(207); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(b'{"accepted":0,"rejected":1,"errors":[{"message":"fixture rejection"}]}')
                return
            for s in batch:
                saved_scores[s['id']] = {**s, 'value': bool(s['value']), 'projectId': 'fixture-project',
                    'source': 'API', 'createdAt': s['timestamp'], 'updatedAt': s['timestamp'],
                    'subject': {'kind': 'observation', 'id': s['observationId'], 'traceId': s['traceId']}}
            self.send_response(202); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(b'{"message":"Accepted"}')
            return
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
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        question = next(str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage))
        if question == 'Wait for provider':
            provider_waiting.set()
            while not provider_release.is_set():
                await asyncio.sleep(.01)
        return self._generate(messages, stop=stop, **kwargs)

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
from langfuse_synth_core.companion import CompanionAdapter
from synth.config import load_config
from synth.companion.app import create_app, REQUIRES_SECRETS
from northwind import config
adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
    health_path='/api/health', langfuse_factory=config.langfuse_factory,
    native_llm_factory=lambda **_: ScriptedProvider())
try:
    with TestClient(create_app(adapter)) as browser:
        session = browser.post('/api/session').json()['session_id']
        reply = browser.post('/api/chat', json={'session_id': session, 'message': 'Show my accounts', 'request_id': 'first'}).json()
        rating = {'session_id': session, 'trace_id': reply['trace_id'], 'observation_id': reply['observation_id'], 'value': 1, 'comment': 'Helpful'}
        first = browser.post('/api/feedback', json=rating)
        assert first.status_code == 200, first.text
        assert first.json()['status'] == 'saved', first.text
        score = score_writes[-1]
        assert score['observationId'] == reply['observation_id']
        assert score['traceId'] == reply['trace_id']
        assert score['dataType'] == 'BOOLEAN' and score['name'] == 'user-thumbs'
        browser.post('/api/chat', json={'session_id': session, 'message': 'Another question', 'request_id': 'second'})
        edited = browser.post('/api/feedback', json={**rating, 'value': 0, 'comment': 'Actually incomplete'})
        assert edited.status_code == 200, edited.text
        assert score_writes[-1]['id'] == score['id']
        assert score_writes[-1]['timestamp'] == score['timestamp']
        assert score_writes[-1]['observationId'] == reply['observation_id']
        assert score_writes[-1]['value'] == 0 and score_writes[-1]['comment'] == 'Actually incomplete'
        hide_feedback = True
        pending_rating = browser.post('/api/feedback', json=rating)
        assert pending_rating.status_code == 202 and pending_rating.json()['status'] == 'pending'
        hide_feedback = False
        verified = browser.get('/api/feedback', params={k: rating[k] for k in ('session_id', 'trace_id', 'observation_id')})
        assert verified.status_code == 200 and verified.json()['status'] == 'saved', verified.text
        reject_feedback = True
        rejected = browser.post('/api/feedback', json=rating)
        assert rejected.status_code == 502 and 'unavailable' in rejected.text
        reject_feedback = False
        forged = browser.post('/api/feedback' , json={**rating, 'observation_id': 'ffffffffffffffff'})
        assert forged.status_code == 403, forged.text
        assert browser.post('/api/feedback', json={**rating, 'value': 2}).status_code == 422
        # New conversation retains banking state; reset restores only this participant.
        action = browser.post('/api/chat', json={'session_id': session, 'message': 'Block my card', 'request_id': 'block'}).json()['banking_actions'][0]
        browser.post('/api/confirm', json={'session_id': session, 'action_id': action['action_id'], 'request_id': 'confirm'})
        before = browser.get('/api/session', params={'session_id': session})
        assert before.status_code == 200, before.text
        run_id = before.json()['run_id']
        new = browser.post('/api/session').json()['session_id']
        assert new != session
        accounts = browser.post('/api/chat', json={'session_id': new, 'message': 'Show my accounts', 'request_id': 'new-accounts'}).json()
        assert 'blocked' in accounts['answer'], accounts
        assert browser.post('/api/feedback', json={**rating, 'value': 1}).status_code == 200
        cookie = browser.cookies.get('northwind_session')
        browser.cookies.clear()
        other = browser.post('/api/session').json()['session_id']
        other_cookie = browser.cookies.get('northwind_session')
        assert browser.post('/api/feedback', json=rating).status_code == 403
        assert browser.post('/api/reset', json={'session_id': new, 'request_id': 'foreign-reset'}).status_code == 409
        browser.cookies.clear(); browser.cookies.set('northwind_session', cookie)
        assert browser.post('/api/reset', json={'session_id': new, 'request_id': 'origin'}, headers={'Origin': 'https://foreign.invalid'}).status_code == 403
        assert browser.post('/api/reset', json={'session_id': new, 'request_id': 'scheme'}, headers={'Origin': 'https://testserver'}).status_code == 403
        assert browser.post('/api/reset', json={'session_id': new, 'request_id': 'claim', 'scenario_id': 'foreign'}).status_code == 422
        reset = browser.post('/api/reset' , json={'session_id': new, 'request_id': 'reset-once'})
        assert reset.status_code == 200, reset.text
        fresh = reset.json()['session_id']
        assert fresh != new and reset.json()['run_id'] != run_id
        assert browser.post('/api/reset', json={'session_id': new, 'request_id': 'reset-once'}).json() == reset.json()
        assert browser.post('/api/confirm', json={'session_id': new, 'action_id': action['action_id'], 'request_id': 'late'}).status_code == 409
        accounts = browser.post('/api/chat', json={'session_id': fresh, 'message': 'Show my accounts', 'request_id': 'after-reset'}).json()
        assert 'active' in accounts['answer'] and 'blocked' not in accounts['answer'], accounts
        assert browser.get('/api/session', params={'session_id': new}).status_code == 409
        browser.cookies.clear(); browser.cookies.set('northwind_session', other_cookie)
        assert browser.get('/api/session', params={'session_id': other}).status_code == 200
        for route in ('/api/run/promote', '/api/run/traffic', '/api/events', '/api/jobs/events'):
            assert browser.get(route).status_code in (403, 404, 405)
            assert browser.post(route, json={'role': 'presenter'}).status_code in (403, 404, 405)
        # Reset stops owned work while another participant can still make progress.
        browser.cookies.clear(); browser.cookies.set('northwind_session', cookie)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(browser.post, '/api/chat', json={'session_id': fresh, 'message': 'Wait for provider', 'request_id': 'slow-reset'})
            assert provider_waiting.wait(5), 'provider did not start'
            independent = browser.post('/api/chat', json={'session_id': other, 'message': 'Show my accounts', 'request_id': 'independent'}, headers={'Cookie': 'northwind_session=' + other_cookie})
            assert independent.status_code == 200, independent.text
            assert browser.post('/api/reset', json={'session_id': fresh, 'request_id': 'reset-during-work'}).status_code == 200
            cancelled = waiting.result(timeout=5)
            assert cancelled.status_code == 409, cancelled.text
        fresh = browser.post('/api/session').json()['session_id']
        provider_waiting.clear()
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(browser.post, '/api/chat', json={'session_id': fresh, 'message': 'Wait for provider', 'request_id': 'slow-cancel'})
            assert provider_waiting.wait(5)
            cancelled = browser.post('/api/cancel', json={'session_id': fresh, 'request_id': 'slow-cancel'})
            assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled', cancelled.text
            assert waiting.result(timeout=5).status_code == 409
            replay = browser.post('/api/chat', json={'session_id': fresh, 'message': 'Wait for provider', 'request_id': 'slow-cancel'})
            assert replay.status_code == 409
        provider_release.set()
    # A replacement application cannot accept a browser's old conversation.
    with TestClient(create_app(adapter)) as replacement:
        replacement.cookies.set('northwind_session', cookie)
        stale = replacement.get('/api/session', params={'session_id': fresh})
        assert stale.status_code == 409 and 'expired' in stale.text, stale.text
        next_session = replacement.post('/api/session').json()
        assert next_session['session_id'] != fresh
        assert next_session['run_id'] != run_id
    print('{"feedback": true}')
finally:
    server.shutdown(); thread.join(); server.server_close()
