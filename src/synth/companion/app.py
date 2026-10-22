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
from langfuse_synth_core.companion import CompanionAdapter, parse_invocation
from langfuse_synth_core.live import paths

STATIC = Path('demos/bank-assistant/portal/static').resolve()
HEALTH_PATH = '/api/health'
REQUIRES_SECRETS = ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LLM_API_KEY')
CUSTOMERS = [{'id': 'C-1001', 'name': 'Ana Torres', 'segment': 'Everyday', 'since': '2017',
              'products': 'Synthetic banking customer'}]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    customer_id: str = 'C-1001'
    session_id: str | None = None
    channel: str = 'web'
    lang: str = 'en'
    prompt_label: str | None = None


def create_app(adapter, *, preview=False):
    from northwind import agent, config, knowledge, lang, prompts
    sessions = {}
    if not preview:
        config.bind_adapter(adapter)

    @asynccontextmanager
    async def lifespan(app):
        yield
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
                  else 'Banking actions, feedback, voice and presenter experiments are unavailable in this candidate.')
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
        return (await asyncio.to_thread(adapter.readiness)).as_dict()

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
                'customers': CUSTOMERS, 'channels': ['web'], 'preview': preview, 'capabilities': {'feedback': False, 'banking_actions': False}}

    @app.post('/api/session')
    async def new_session(response: Response, request: Request):
        if len(sessions) >= 128:
            sessions.pop(next(iter(sessions)))
        token = secrets.token_urlsafe(32)
        session_id = 'nw-' + secrets.token_hex(16)
        sessions[token] = {'session_id': session_id, 'history': [], 'lock': asyncio.Lock()}
        response.set_cookie('northwind_session', token, httponly=True, samesite='strict', path=paths.local('/'), secure=request.headers.get('x-forwarded-proto', request.url.scheme) == 'https')
        return {'session_id': session_id}

    @app.post('/api/chat')
    async def chat(body: ChatIn, request: Request):
        token = request.cookies.get('northwind_session')
        if not token or token not in sessions or sessions[token]['session_id'] != body.session_id:
            raise HTTPException(409, 'Start a new conversation for this deployment')
        if body.customer_id != 'C-1001':
            raise HTTPException(400, 'Customer is not available')
        state = sessions[token]
        session_id = state['session_id']
        if state['lock'].locked():
            raise HTTPException(409, 'A reply is already running')
        async with state['lock']:
            started = time.monotonic()
            if preview:
                check = agent.assess_input(body.message, 'C-1001')
                language = lang.detect(body.message)
                docs = knowledge.search(body.message, k=1, min_score=0.08)
                answer = (agent.REFUSAL[language] if check['blocked'] else
                          ('La transferencia internacional cuesta USD 35. [KB-202]' if language == 'es'
                           else 'An international wire costs USD 35. [KB-202]'))
                return {'answer': answer, 'preview': True, 'trace_url': '', 'trace_id': '', 'apm_url': '',
                        'session_id': session_id, 'blocked': check['blocked'], 'risks': check['risks'],
                        'sources': ['KB-202'], 'retrieved': [d['id'] for d in docs], 'tools_used': [],
                        'model': 'Fixture response', 'latency_ms': 0, 'turn': 1}
            try:
                result = await asyncio.wait_for(agent.run_turn(body.message, customer_id='C-1001', session_id=session_id,
                    history=state['history'], channel='web', prompt_label='production', tags=['source:depot']), timeout=120)
                await asyncio.to_thread(config.flush)
            except Exception as exc:
                raise HTTPException(502, 'Banking response unavailable: ' + type(exc).__name__) from None
            if result.get('error'):
                raise HTTPException(502, 'The banking response did not complete')
            state['history'] = (state['history'] + [{'role': 'user', 'content': body.message},
                                                  {'role': 'assistant', 'content': result['answer']}])[-20:]
            return {**result, 'session_id': session_id, 'turn': len(state['history']) // 2, 'prompt_label': 'production', 'latency_ms': round((time.monotonic() - started) * 1000)}

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
