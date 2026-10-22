"""Delivered banking HTTP surface; no workstation launchers or arbitrary jobs."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import hashlib
from langfuse_synth_core.companion import CompanionAdapter, parse_invocation
from langfuse_synth_core.live import paths

STATIC = Path('demos/bank-assistant/portal/static').resolve()
HEALTH_PATH = '/api/health'
REQUIRES_SECRETS = ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LLM_API_KEY')
CUSTOMERS = [
    {'id': cid, 'name': name, 'segment': segment, 'since': since,
     'products': 'Synthetic banking customer'}
    for cid, name, segment, since in (
        ('C-1001', 'Ana Torres', 'Everyday', '2017'),
        ('C-1002', 'Ben Okafor', 'Premier', '2012'),
        ('C-1003', 'Carla Mendes', 'Everyday', '2022'),
        ('C-1004', 'David Kim', 'Premier', '2015'),
    )
]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    customer_id: str = 'C-1001'
    session_id: str | None = None
    channel: str = 'web'
    lang: str = 'en'
    prompt_label: str | None = None
    request_id: str = Field(default_factory=lambda: secrets.token_hex(16), min_length=1, max_length=128)


class SessionIn(BaseModel):
    customer_id: str = 'C-1001'


class ConfirmIn(BaseModel):
    session_id: str
    action_id: str
    request_id: str = Field(min_length=1, max_length=128)
    lang: str = 'en'


def create_app(adapter, *, preview=False):
    from northwind import agent, config, knowledge, lang, prompts
    sessions = {}
    from .supervisor import BankingService
    banking = None if preview else BankingService(adapter)
    if not preview:
        config.bind_adapter(adapter)

    @asynccontextmanager
    async def lifespan(app):
        try:
            if banking:
                try:
                    await banking.start()
                except Exception as exc:
                    banking.error = type(exc).__name__
                    print('Banking startup failed: ' + banking.error, flush=True)
                    await banking.stop()
                config.MCP_ENABLED = True
                config.MCP_URL = banking.url
            yield
        finally:
            tasks = [r['task'] for s in sessions.values() for r in s.get('requests', {}).values() if not r['task'].done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if banking:
                await banking.stop()
            if not preview:
                await asyncio.to_thread(config.shutdown)

    app = FastAPI(title='Northwind Bank', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.middleware('http')
    async def same_origin(request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD') and origin:
            expected = request.headers.get('x-forwarded-host', request.headers.get('host'))
            if urlparse(origin).netloc != expected:
                return Response('Request origin does not match this deployment', status_code=403)
        return await call_next(request)

    @app.get('/', response_class=HTMLResponse)
    async def index():
        content = (STATIC / 'index.html').read_text()
        prefix = paths.base_path()
        content = content.replace('"/static/', f'"{escape(prefix)}/static/').replace('href="/"', f'href="{escape(prefix)}/"')
        setting = json.dumps(prefix).replace('<', '\\u003c')
        content = content.replace('<head>', '<head><script>window.NORTHWIND_BASE_PATH=' + setting + ';</script>')
        notice = ('Development preview — fixture responses only; no target or provider connection.' if preview
                  else 'Synthetic banking state is private to this participant. Confirm each banking action before it runs.')
        content = content.replace('id="labelSel"', 'id="labelSel" disabled title="Only the production prompt is enabled"').replace('data-ch="app"', 'data-ch="app" disabled').replace('data-ch="whatsapp"', 'data-ch="whatsapp" disabled')
        content = content.replace('<body>', '<body><aside role="status" style="padding:12px;text-align:center">' + escape(notice) + '</aside>')
        if preview:
            import re
            content = re.sub(r'\s*<link[^>]+(?:fonts.googleapis.com|fonts.gstatic.com)[^>]*>', '', content)
            content = content.replace('Traced · PII-masked', 'Fixture preview').replace('data-i18n="chat.traced"', '').replace('title="Every turn is traced in Langfuse"', 'title="Fixture preview"').replace('data-i18n-title="chat.tracedTitle"', '')
        return content

    @app.get('/api/health')
    async def health():
        if preview:
            return JSONResponse({'ready': False, 'langfuse_write_ok': False, 'llm_bound': False, 'preview': True}, status_code=503)
        report = (await asyncio.to_thread(adapter.readiness)).as_dict()
        report['banking_ready'] = banking.ready
        report['ready'] = report['ready'] and banking.ready
        report['detail']['banking'] = {'ok': banking.ready}
        if not banking.ready:
            report['detail']['banking']['error'] = banking.error or 'BankingProcessExited'
        return report

    @app.get('/api/info')
    async def info():
        pid, base, prompt = None, '', {'ok': False, 'error': 'Prompt unavailable'}
        model = 'Fixture response' if preview else 'Unbound'
        if not preview:
            base = adapter.base_url
            try:
                pid = (await asyncio.to_thread(adapter.read_json, '/api/public/projects'))['data'][0]['id']
                native = adapter.native_llm()
                model = getattr(native, 'model', None) or getattr(native, 'model_name', 'Configured model')
                p = await asyncio.to_thread(adapter.langfuse().get_prompt, prompts.PROMPT_NAME, label='production', max_retries=0, fetch_timeout_seconds=3)
                prompt = {'ok': True, 'version': p.version}
            except Exception:
                pass  # Configuration remains visible when a dependency is unavailable.
        project_url = f'{base}/project/{pid}' if pid else ''
        return {'environment': 'preview' if preview else config.ENVIRONMENT, 'profile': 'depot',
                'langfuse_base_url': base, 'langfuse_target': 'Fixture preview' if preview else 'Configured Langfuse target',
                'project_id': pid, 'project_url': project_url, 'prompt_name': prompts.PROMPT_NAME,
                'prompt_label': 'production', 'production_prompt': prompt, 'model': model, 'release': config.RELEASE,
                'links': [{'label': 'Langfuse project', 'url': project_url}] if project_url else [],
                'customers': CUSTOMERS, 'channels': ['web'], 'preview': preview,
                'capabilities': {'feedback': False, 'banking_actions': bool(banking and banking.ready)}}

    def owned(request, session_id, customer_id=None):
        token = request.cookies.get('northwind_session')
        state = sessions.get(token)
        if not state or state['session_id'] != session_id:
            raise HTTPException(409, 'Start a new conversation for this deployment')
        if customer_id is not None and customer_id != state['customer_id']:
            raise HTTPException(403, 'Customer does not match this conversation')
        return state

    @app.post('/api/session')
    async def new_session(response: Response, request: Request, body: SessionIn | None = None):
        customer_id = body.customer_id if body else 'C-1001'
        if customer_id not in {c['id'] for c in CUSTOMERS}:
            raise HTTPException(400, 'Customer is not available')
        token = request.cookies.get('northwind_session')
        previous = sessions.get(token)
        if previous and (previous['lock'].locked()
                         or any(not r['task'].done() for r in previous['requests'].values())):
            raise HTTPException(409, 'A reply is already running')
        if previous is None:
            if len(sessions) >= 128:
                raise HTTPException(429, 'Participant capacity reached')
            token = secrets.token_urlsafe(32)
        scenarios = previous['scenarios'] if previous else {}
        scenario_id = scenarios.setdefault(customer_id, secrets.token_hex(16))
        session_id = 'nw-' + secrets.token_hex(16)
        sessions[token] = {'session_id': session_id, 'scenario_id': scenario_id,
                           'scenarios': scenarios, 'customer_id': customer_id,
                           'history': [], 'lock': asyncio.Lock(), 'pending': {}, 'requests': {}}
        response.set_cookie('northwind_session', token, httponly=True, samesite='strict',
                            path=paths.local('/'), secure=request.headers.get('x-forwarded-proto', request.url.scheme) == 'https')
        return {'session_id': session_id, 'customer_id': customer_id}

    async def execute(state, body, confirmation=None):
        async with state['lock']:
            started = time.monotonic()
            session_id, customer_id = state['session_id'], state['customer_id']
            if preview:
                check = agent.assess_input(body.message, customer_id)
                language = lang.detect(body.message)
                docs = knowledge.search(body.message, k=1, min_score=0.08)
                answer = (agent.REFUSAL[language] if check['blocked'] else
                          ('La transferencia internacional cuesta USD 35. [KB-202]' if language == 'es'
                           else 'An international wire costs USD 35. [KB-202]'))
                return {'answer': answer, 'preview': True, 'trace_url': '', 'trace_id': '', 'apm_url': '',
                        'session_id': session_id, 'blocked': check['blocked'], 'risks': check['risks'],
                        'sources': ['KB-202'], 'retrieved': [d['id'] for d in docs], 'tools_used': [],
                        'model': 'Fixture response', 'latency_ms': 0, 'turn': 1}
            if not banking.ready:
                raise HTTPException(503, 'Banking service unavailable; start a new deployment')
            binding = {'instance': banking.instance, 'epoch': banking.epoch,
                       'scenario_id': state['scenario_id'], 'customer_id': customer_id,
                       'session_id': session_id, 'request_id': body.request_id}
            if confirmation:
                binding['confirmation_id'] = confirmation['action_id']
            from northwind.binding import sign
            try:
                result = await agent.run_turn(body.message, customer_id=customer_id, session_id=session_id,
                    history=state['history'], channel='web', prompt_label='production', tags=['source:depot'],
                    extra_metadata={'live_instance_id': banking.instance, 'live_run_id': banking.epoch},
                    banking_binding=sign(binding, banking.secret), confirmation=confirmation)
                await asyncio.to_thread(config.flush)
            except Exception as exc:
                raise HTTPException(502, 'Banking response unavailable: ' + type(exc).__name__) from None
            if result.get('error') and not any(a.get('status') in ('blocked', 'opened', 'scheduled')
                                              for a in result.get('banking_actions', [])):
                raise HTTPException(502, 'The banking response did not complete; retry with the same request identity')
            for action in result.get('banking_actions', []):
                if action.get('status') == 'confirmation_required':
                    state['pending'][action['action_id']] = action
            state['history'] = (state['history'] + [{'role': 'user', 'content': body.message},
                                                  {'role': 'assistant', 'content': result['answer']}])[-20:]
            return {**result, 'session_id': session_id, 'request_id': body.request_id,
                    'turn': len(state['history']) // 2, 'prompt_label': 'production',
                    'latency_ms': round((time.monotonic() - started) * 1000)}

    async def dispatch(state, body, confirmation=None):
        fingerprint = hashlib.sha256(json.dumps([body.model_dump(), confirmation], sort_keys=True).encode()).hexdigest()
        existing = state['requests'].get(body.request_id)
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise HTTPException(409, 'Request identity was already used with different input')
            task = existing['task']
        else:
            if state['lock'].locked() or any(not r['task'].done() for r in state['requests'].values()):
                raise HTTPException(409, 'A reply is already running')
            if len(state['requests']) >= 128:
                raise HTTPException(429, 'Start a new conversation to continue')
            task = asyncio.create_task(execute(state, body, confirmation))
            state['requests'][body.request_id] = {'fingerprint': fingerprint, 'task': task}
        try:
            # A transport timeout/disconnect must not cancel an already committed action.
            return await asyncio.wait_for(asyncio.shield(task), 120)
        except TimeoutError:
            raise HTTPException(503, 'Reply still running; retry with the same request identity') from None

    @app.post('/api/chat')
    async def chat(body: ChatIn, request: Request):
        return await dispatch(owned(request, body.session_id, body.customer_id), body)

    @app.post('/api/confirm')
    async def confirm(body: ConfirmIn, request: Request):
        state = owned(request, body.session_id)
        action = state['pending'].get(body.action_id)
        if not action or preview:
            raise HTTPException(409, 'This action is not pending in this conversation')
        message = ('Confirmo la acción indicada.' if body.lang == 'es' else 'I confirm the displayed action.')
        turn = ChatIn(session_id=body.session_id, customer_id=state['customer_id'],
                      request_id=body.request_id, message=message, lang=body.lang)
        return await dispatch(state, turn, action)

    @app.get('/api/voice')
    async def voice():
        return {'available': False, 'error': 'Voice is not enabled in this candidate', 'samples': []}

    @app.get('/api/acts')
    async def acts():
        return {'acts': [{'id': 'pending', 'group': 'Configuration', 'title': 'Presenter actions',
                         'available': False, 'blurb': 'Experiments and promotion are not enabled in this candidate.',
                         'show': 'Use the delivered runbook for available evidence.', 'command': '', 'button': 'Run'}],
                'groups': {}, 'job': None}

    @app.post('/api/feedback')
    async def feedback():
        raise HTTPException(409, 'Feedback requires observation ownership support, which is pending')

    return app


def main(argv=None):
    from northwind import config
    from synth.config import load_config
    invocation = parse_invocation(argv)
    adapter = CompanionAdapter(load_config(invocation.config), requires_secrets=REQUIRES_SECRETS,
        health_path=HEALTH_PATH, llm_model_default=config.AGENT_MODEL,
        langfuse_factory=config.langfuse_factory, native_llm_factory=config.native_llm_factory)
    adapter.run(create_app, host=invocation.host, port=invocation.port)
    return 0
