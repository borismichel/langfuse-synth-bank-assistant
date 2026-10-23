"""Delivered banking HTTP surface; no workstation launchers or arbitrary jobs."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Literal
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
    model_config = {'extra': 'forbid'}
    customer_id: str = 'C-1001'


class ConfirmIn(BaseModel):
    session_id: str
    action_id: str
    request_id: str = Field(min_length=1, max_length=128)
    lang: str = 'en'


class FeedbackIn(BaseModel):
    model_config = {'extra': 'forbid'}
    session_id: str
    trace_id: str
    observation_id: str
    value: Literal[0, 1]
    comment: str = Field(default='', max_length=2000)


class SessionAction(BaseModel):
    model_config = {'extra': 'forbid'}
    session_id: str
    request_id: str = Field(min_length=1, max_length=128)


def create_app(adapter, *, preview=False):
    from northwind import agent, config, knowledge, lang, prompts
    sessions = {}
    from .supervisor import BankingService
    from .jaeger import JaegerService
    from .apm_routes import apm_enabled, mount_apm
    apm = JaegerService(enabled=False if preview else apm_enabled())
    banking = None if preview else BankingService(adapter)
    from .runtime_options import enabled
    from .workflows import WorkflowService, mount_workflow
    workflow = WorkflowService(adapter, enabled=not preview and enabled('n8n_enabled'))
    if not preview:
        config.bind_adapter(adapter)

    @asynccontextmanager
    async def lifespan(app):
        try:
            if apm.enabled:
                import os
                from langfuse_synth_core.companion.access import route_access, validate_protected_paths
                policy = validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS', '[]')))
                if not route_access('/apm', policy) or not os.environ.get('PORTAL_LIVE_PROOF'):
                    apm.error = 'APMProtectionMissing'
                else:
                    await apm.start()
                if apm.ready:
                    config.configure_apm(apm.otlp_url, paths.base_path() + '/apm/trace/{trace_id}')
                    banking.apm_endpoint = apm.otlp_url
            if banking:
                try:
                    await banking.start()
                except Exception as exc:
                    banking.error = type(exc).__name__
                    print('Banking startup failed: ' + banking.error, flush=True)
                    await banking.stop()
                config.MCP_ENABLED = True
                config.MCP_URL = banking.url
            await workflow.start()
            await judge_controls.start()
            yield
        finally:
            tasks = [r['task'] for s in sessions.values() for group in ('requests', 'resets')
                     for r in s.get(group, {}).values() if not r['task'].done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await experiment_controls.stop()
            await review_controls.stop()
            await judge_controls.stop()
            await workflow.stop()
            if banking:
                await banking.stop()
            try:
                if not preview:
                    await asyncio.to_thread(config.shutdown)
            finally:
                await apm.stop()

    app = FastAPI(title='Northwind Bank', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount('/static', StaticFiles(directory=STATIC), name='static')
    mount_apm(app, apm)
    from .judge_routes import mount_judges
    judge_controls = mount_judges(app, adapter, banking, preview=preview)
    mount_workflow(app, workflow)
    from .experiments import mount_experiments
    experiment_controls = mount_experiments(app, adapter, banking, preview=preview)
    from .provider_comparison import mount_provider_comparison
    mount_provider_comparison(app, experiment_controls)
    from .review_routes import mount_reviews
    review_controls = mount_reviews(app, adapter, banking, preview=preview)

    @app.middleware('http')
    async def same_origin(request, call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD') and origin:
            expected_host = request.headers.get('x-forwarded-host', request.headers.get('host'))
            expected_scheme = request.headers.get('x-forwarded-proto', request.url.scheme)
            supplied = urlparse(origin)
            if (supplied.netloc.lower() != expected_host.lower()
                    or supplied.scheme.lower() != expected_scheme.lower()):
                return Response('Request origin does not match this deployment', status_code=403)
        return await call_next(request)

    @app.get('/', response_class=HTMLResponse)
    async def index():
        content = (STATIC / 'index.html').read_text()
        prefix = paths.base_path()
        content = content.replace('"/static/', f'"{escape(prefix)}/static/').replace('href="/"', f'href="{escape(prefix)}/"')
        content = content.replace('<nav class="tabs" role="tablist">', '<nav class="tabs" role="tablist"><a class="history-link" style="padding:12px 16px;color:inherit" href="' + escape(prefix) + '/history">Historical evidence</a>')
        if not preview:
            content = content.replace('<nav class="tabs" role="tablist">', '<nav class="tabs" role="tablist"><a class="history-link" style="padding:12px 16px;color:inherit" href="' + escape(prefix) + '/manage/experiments">Application experiments</a>')
            content = content.replace('<nav class="tabs" role="tablist">', '<nav class="tabs" role="tablist"><a class="history-link" style="padding:12px 16px;color:inherit" href="' + escape(prefix) + '/manage/judges">Policy judges</a>')
        if not preview:
            content = content.replace('</main>', '<footer style="padding:16px;color:#64748b"><details><summary>Presenter actions</summary><a href="' + escape(prefix) + '/manage/reviews">Review and calibration</a></details></footer></main>')
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

    @app.get('/api/history')
    async def historical_evidence():
        from .history import history_view
        return history_view(adapter, preview=preview)

    @app.get('/history', response_class=HTMLResponse)
    async def historical_page():
        from .history import history_page
        return history_page(adapter, preview=preview)

    @app.get('/api/health')
    async def health():
        if preview:
            return JSONResponse({'ready': False, 'langfuse_write_ok': False, 'llm_bound': False, 'preview': True}, status_code=503)
        report = (await asyncio.to_thread(adapter.readiness)).as_dict()
        report['banking_ready'] = banking.ready
        report['ready'] = report['ready'] and banking.ready
        report['detail']['banking'] = {'ok': banking.ready}
        report['detail']['workflow'] = workflow.detail()
        if workflow.enabled:
            report['ready'] = report['ready'] and workflow.ready
        if not banking.ready:
            report['detail']['banking']['error'] = banking.error or 'BankingProcessExited'
        report['detail']['apm'] = {'enabled': apm.enabled, 'ok': apm.ready if apm.enabled else True}
        if apm.enabled and not apm.ready:
            report['ready'] = False
            report['detail']['apm']['error'] = apm.error or 'APMProcessExited'
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
                'links': ([{'label': 'Langfuse project', 'url': project_url}] if project_url else []) +
                         ([{'label': 'APM', 'url': paths.base_path() + '/apm/'}] if apm.ready else []) +
                         ([{'label': 'Complaint workflow', 'url': paths.base_path() + '/n8n/workflow/northwindComplaint'}] if workflow.ready else []),
                'customers': CUSTOMERS, 'channels': ['web'], 'preview': preview,
                'capabilities': {'feedback': not preview, 'banking_actions': bool(banking and banking.ready)}}

    def owned(request, session_id, customer_id=None):
        token = request.cookies.get('northwind_session')
        state = sessions.get(token)
        if not state or state['session_id'] != session_id:
            raise HTTPException(409, 'Conversation expired or was reset. Start a new conversation; previous banking state may be unavailable.')
        if state.get('resetting'):
            raise HTTPException(409, 'Scenario reset is in progress')
        if customer_id is not None and customer_id != state['customer_id']:
            raise HTTPException(403, 'Customer does not match this conversation')
        return state

    def session_view(state):
        return {'session_id': state['session_id'], 'customer_id': state['customer_id'],
                'run_id': state['scenario']['run_id'], 'ephemeral': True}

    def replace_conversation(state):
        state.update(session_id='nw-' + secrets.token_hex(16), history=[], pending={}, requests={},
                     lock=asyncio.Lock(), resetting=False)
        return session_view(state)

    @app.get('/api/session')
    async def session_status(request: Request, session_id: str):
        return session_view(owned(request, session_id))

    @app.post('/api/session')
    async def new_session(response: Response, request: Request, body: SessionIn | None = None):
        customer_id = body.customer_id if body else 'C-1001'
        if customer_id not in {c['id'] for c in CUSTOMERS}:
            raise HTTPException(400, 'Customer is not available')
        token = request.cookies.get('northwind_session')
        previous = sessions.get(token)
        if previous and (previous.get('resetting') or previous['lock'].locked()
                         or any(not r['task'].done() for r in previous['requests'].values())):
            raise HTTPException(409, 'A reply is already running')
        if previous is None:
            if len(sessions) >= 128:
                raise HTTPException(429, 'Participant capacity reached')
            token = secrets.token_urlsafe(32)
        scenarios = previous['scenarios'] if previous else {}
        scenario = scenarios.setdefault(customer_id, {'id': secrets.token_hex(16), 'revision': 0,
                                                       'run_id': secrets.token_hex(16)})
        session_id = 'nw-' + secrets.token_hex(16)
        sessions[token] = {'session_id': session_id, 'scenario_id': scenario['id'], 'scenario': scenario,
                           'scenarios': scenarios, 'customer_id': customer_id,
                           'history': [], 'lock': asyncio.Lock(), 'pending': {}, 'requests': {},
                           'answers': previous['answers'] if previous else {},
                           'resets': previous['resets'] if previous else {}}
        response.set_cookie('northwind_session', token, httponly=True, samesite='strict',
                            path=paths.local('/'), secure=request.headers.get('x-forwarded-proto', request.url.scheme) == 'https')
        return session_view(sessions[token])

    async def cancel_work(state):
        tasks = [entry['task'] for entry in state['requests'].values() if not entry['task'].done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    @app.post('/api/cancel')
    async def cancel(body: SessionAction, request: Request):
        state = owned(request, body.session_id)
        entry = state['requests'].get(body.request_id)
        if not entry:
            raise HTTPException(404, 'Request is not part of this conversation')
        task = entry['task']
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return {'status': 'cancelled' if task.cancelled() else 'completed',
                'request_id': body.request_id,
                'detail': 'Cancellation stops pending work; it does not undo completed banking actions or provider charges.'}

    @app.post('/api/reset')
    async def reset(body: SessionAction, request: Request):
        state = sessions.get(request.cookies.get('northwind_session'))
        if state is None:
            raise HTTPException(409, 'Start a new conversation for this deployment')
        existing = state['resets'].get(body.request_id)
        if existing:
            if existing['session_id'] != body.session_id:
                raise HTTPException(409, 'Reset identity was used for another conversation')
            return await asyncio.shield(existing['task'])
        owned(request, body.session_id)
        if len(state['resets']) >= 128:
            raise HTTPException(429, 'Reset capacity reached for this participant')
        state['resetting'] = True

        async def restore():
            await cancel_work(state)
            try:
                revision = (state['scenario']['revision'] + 1 if preview else
                            await asyncio.wait_for(banking.reset(state), 15))
            except Exception as exc:
                # Keep the scenario closed after an uncertain reset; redeployment is explicit recovery.
                raise HTTPException(503, 'Reset could not be confirmed; redeploy: ' + type(exc).__name__) from None
            state['scenario'].update(revision=revision, run_id=secrets.token_hex(16))
            return {**replace_conversation(state), 'status': 'reset', 'history_retained': True}

        task = asyncio.create_task(restore())
        state['resets'][body.request_id] = {'session_id': body.session_id, 'task': task}
        return await asyncio.shield(task)

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
                       'session_id': session_id, 'request_id': body.request_id,
                       'revision': state['scenario']['revision']}
            if confirmation:
                binding['confirmation_id'] = confirmation['action_id']
            from northwind.binding import sign
            try:
                result = await agent.run_turn(body.message, customer_id=customer_id, session_id=session_id,
                    history=state['history'], channel='web', prompt_label='production', tags=['source:depot'],
                    extra_metadata={'live_instance_id': banking.instance, 'live_run_id': state['scenario']['run_id']},
                    banking_binding=sign(binding, banking.secret), confirmation=confirmation)
                await asyncio.to_thread(config.flush)
            except Exception as exc:
                raise HTTPException(502, 'Banking response unavailable: ' + type(exc).__name__) from None
            if state['session_id'] != session_id or state.get('resetting'):
                raise HTTPException(409, 'The conversation was reset while this request was running')
            if result.get('error') and not any(a.get('status') in ('blocked', 'opened', 'scheduled')
                                              for a in result.get('banking_actions', [])):
                raise HTTPException(502, 'The banking response did not complete; retry with the same request identity')
            for action in result.get('banking_actions', []):
                if action.get('status') == 'confirmation_required':
                    state['pending'][action['action_id']] = action
            state['history'] = (state['history'] + [{'role': 'user', 'content': body.message},
                                                  {'role': 'assistant', 'content': result['answer']}])[-20:]
            subject = (result['trace_id'], result['observation_id'])
            timestamp = datetime.now(timezone.utc)
            timestamp = timestamp.replace(microsecond=timestamp.microsecond // 1000 * 1000)
            state['answers'][subject] = {'id': secrets.token_hex(16), 'timestamp': timestamp,
                'trace_id': result['trace_id'], 'observation_id': result['observation_id'],
                'session_id': session_id, 'run_id': state['scenario']['run_id'], 'environment': config.ENVIRONMENT,
                'lock': asyncio.Lock()}
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
            if len(state['answers']) >= 512:
                raise HTTPException(429, 'Participant answer capacity reached; start a new deployment')
            if len(state['requests']) >= 128:
                raise HTTPException(429, 'Start a new conversation to continue')
            task = asyncio.create_task(execute(state, body, confirmation))
            state['requests'][body.request_id] = {'fingerprint': fingerprint, 'task': task}
        try:
            # A transport timeout/disconnect must not cancel an already committed action.
            return await asyncio.wait_for(asyncio.shield(task), 120)
        except TimeoutError:
            raise HTTPException(503, 'Reply still running; retry with the same request identity') from None
        except asyncio.CancelledError:
            if task.cancelled():
                raise HTTPException(409, 'Request was cancelled. Start a new request to continue.') from None
            raise

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

    def owned_answer(request, session_id, trace_id, observation_id):
        state = sessions.get(request.cookies.get('northwind_session'))
        answer = state['answers'].get((trace_id, observation_id)) if state else None
        if not answer or answer['session_id'] != session_id or preview:
            raise HTTPException(403, 'This answer does not belong to this participant')
        return answer

    def feedback_result(answer, status):
        return {'id': answer['id'], 'timestamp': answer['timestamp'].isoformat(),
                'status': status, 'value': answer['value'], 'comment': answer['comment'],
                'trace_id': answer['trace_id'], 'observation_id': answer['observation_id']}

    @app.post('/api/feedback')
    async def feedback(body: FeedbackIn, request: Request):
        from .feedback import write
        answer = owned_answer(request, body.session_id, body.trace_id, body.observation_id)
        async with answer['lock']:
            answer.update(value=body.value, comment=body.comment)
            try:
                status = await asyncio.to_thread(write, adapter.langfuse(), answer)
            except Exception as exc:
                raise HTTPException(502, 'Feedback unavailable: ' + type(exc).__name__) from None
            return JSONResponse(feedback_result(answer, status), status_code=200 if status == 'saved' else 202)

    @app.get('/api/feedback')
    async def feedback_status(request: Request, session_id: str, trace_id: str, observation_id: str):
        from .feedback import read
        answer = owned_answer(request, session_id, trace_id, observation_id)
        if 'value' not in answer:
            raise HTTPException(404, 'No feedback has been submitted for this answer')
        async with answer['lock']:
            try:
                status = await asyncio.to_thread(read, adapter.langfuse(), answer)
            except Exception as exc:
                raise HTTPException(502, 'Feedback verification unavailable: ' + type(exc).__name__) from None
            return feedback_result(answer, status)

    return app


def main(argv=None):
    from northwind import config
    from synth.config import load_config
    invocation = parse_invocation(argv)
    adapter = CompanionAdapter(load_config(invocation.config), requires_secrets=REQUIRES_SECRETS,
        health_path=HEALTH_PATH, llm_model_default=config.AGENT_MODEL,
        langfuse_factory=config.langfuse_factory, native_llm_factory=config.native_llm_factory,
        role_factories={'comparison':config.native_llm_factory})
    adapter.run(create_app, host=invocation.host, port=invocation.port)
    return 0
