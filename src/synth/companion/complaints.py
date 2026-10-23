"""Authenticated loopback complaint steps; model and target credentials stay here."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import socket
import time
from collections import deque
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
import uvicorn

from synth.complaint_prompt import PROMPT_NAME
DRAFT = '''You draft replies for Northwind Bank, a fictional bank. Write at most 120 words in the customer's language.
Acknowledge the complaint and explain that customer care will review it. Never claim a card has been blocked,
a dispute opened, a refund issued or a formal complaint filed: this workflow classifies and drafts only.
For fraud, advise contacting the 24/7 fraud team using the number on the card. For regulatory escalation,
explain that the complaint needs formal review. Never request PINs, passwords, one-time codes or full card numbers.
Do not promise compensation or a guaranteed outcome. Sign as Northwind Customer Care.'''


class ComplaintBridge:
    def __init__(self, adapter, *, instance_id, base_path):
        self.adapter = adapter
        self.instance_id = instance_id
        self.base_path = base_path
        self.token = secrets.token_urlsafe(32)
        self.runs = {}
        self.lock = asyncio.Lock()
        self.export_lock = asyncio.Lock()
        self.export_times = deque()
        self.start_times = deque()
        self.export_receipts = {}
        self.server = None
        self.task = None
        self.url = ''
        self.error = None
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.add_api_route('/{stage}', self.handle, methods=['POST'])

    async def start(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        self.url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='error', access_log=False, timeout_graceful_shutdown=5))
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
        async with asyncio.timeout(5):
            while not self.server.started:
                await asyncio.sleep(.01)

    @contextmanager
    def attributes(self, run):
        with propagate_attributes(trace_name='n8n-complaint-triage', user_id=run['customer'],
                session_id=run['session'], tags=['channel:n8n', 'team:customer-care'],
                metadata={'origin': 'n8n-complaint', 'live_run_id': self.instance_id,
                          'conduct_eligible': 'false', 'execution_id': run['execution_id']}):
            yield

    async def handle(self, stage: str, request: Request):
        if not secrets.compare_digest(request.headers.get('x-northwind-workflow', ''), self.token):
            raise HTTPException(404)
        if stage == 'otel':
            return await self.native(request)
        if stage not in {'mask', 'classify', 'reply'}:
            raise HTTPException(404)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 16384:
                raise HTTPException(413, 'Complaint request exceeds limit')
        try:
            body = json.loads(data)
            execution = body['execution_id']
            if not isinstance(execution, str) or not re.fullmatch(r'[0-9]{1,24}', execution):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'Invalid complaint request') from None
        if self.lock.locked():
            raise HTTPException(429, 'A complaint step is already running')
        async with self.lock:
            self._expire()
            if stage == 'mask':
                return await self.mask(body, request.headers.get('traceparent', ''))
            run = self.runs.get(execution)
            if not run or not secrets.compare_digest(str(body.get('run_token', '')), run['token']):
                raise HTTPException(409, 'Complaint execution expired')
            if stage in run['results']:
                return run['results'][stage]
            if run.get('failed') or (stage == 'reply' and 'classify' not in run['results']):
                raise HTTPException(409, 'Complaint step cannot run in this state')
            try:
                result = await self.generate(run, stage)
                run['results'][stage] = result
                return result
            except Exception as exc:
                run['failed'] = True
                run['root'].update(level='ERROR', status_message=type(exc).__name__)
                run['root'].end()
                raise HTTPException(502, 'Complaint step failed; inspect its observation and start a new execution') from None

    async def native(self, request):
        from .complaint_telemetry import project_native
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
        if self.export_lock.locked():
            raise HTTPException(429, 'Native export is busy')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise HTTPException(413)
        digest = hashlib.sha256(raw).hexdigest()
        if digest in self.export_receipts:
            return Response(self.export_receipts[digest], media_type='application/x-protobuf')
        now = time.monotonic()
        while self.export_times and now - self.export_times[0] > 60:
            self.export_times.popleft()
        if len(self.export_times) >= 30:
            raise HTTPException(429, 'Native export rate exceeded')
        self.export_times.append(now)
        try:
            payload = project_native(bytes(raw), self.runs)
        except Exception:
            raise HTTPException(400, 'Invalid native workflow telemetry') from None
        if not payload:
            return Response(b'', media_type='application/x-protobuf')
        async with self.export_lock:
            try:
                result = await asyncio.to_thread(self.adapter.forward_otel, payload)
                report = ExportTraceServiceResponse.FromString(result)
                if report.partial_success.rejected_spans or report.partial_success.error_message:
                    self.error = 'NativeTraceExportRejected'
                self.export_receipts[digest] = result
                if len(self.export_receipts) > 256:
                    del self.export_receipts[next(iter(self.export_receipts))]
                return Response(result, media_type='application/x-protobuf')
            except Exception:
                self.error = 'NativeTraceExportFailed'
                raise HTTPException(503, 'Native workflow telemetry was not confirmed') from None

    async def mask(self, body, carrier):
        from northwind.masking import scrub
        complaint = body.get('complaint')
        if not isinstance(complaint, dict):
            raise HTTPException(400, 'Invalid complaint')
        text = complaint.get('text')
        customer = complaint.get('customer_id', 'C-1001')
        if not isinstance(text, str) or not 1 <= len(text) <= 6000 or customer not in {'C-1001', 'C-1002', 'C-1003', 'C-1004'}:
            raise HTTPException(400, 'Use a synthetic customer and 1–6000 characters')
        match = re.fullmatch(r'00-([a-f0-9]{32})-([a-f0-9]{16})-01', carrier)
        if not match or int(match[1], 16) == 0 or int(match[2], 16) == 0:
            raise HTTPException(409, 'Native workflow trace context is unavailable')
        execution = body['execution_id']
        fingerprint = hashlib.sha256(json.dumps(complaint, sort_keys=True).encode()).hexdigest()
        if execution in self.runs:
            run = self.runs[execution]
            if run['fingerprint'] != fingerprint or run['trace_id'] != match[1]:
                raise HTTPException(409, 'Complaint execution already exists with different input')
            return run['results']['mask']
        now = time.monotonic()
        while self.start_times and now - self.start_times[0] > 3600:
            self.start_times.popleft()
        if len(self.start_times) >= 20:
            raise HTTPException(429, 'This instance has reached 20 complaint executions per hour')
        self.start_times.append(now)
        if len(self.runs) >= 100:
            raise HTTPException(429, 'Complaint history is full; restart the live instance')
        masked, categories = scrub(text)
        run = {'execution_id': execution, 'fingerprint': fingerprint, 'token': secrets.token_urlsafe(24),
            'trace_id': match[1], 'customer': customer, 'text': masked, 'masked': sorted(categories),
            'session': 'n8n-' + self.instance_id + '-' + execution, 'results': {}, 'created': time.monotonic()}
        client = self.adapter.langfuse()
        with self.attributes(run):
            root = client.start_observation(name='n8n-complaint-triage', as_type='chain',
                trace_context={'trace_id': match[1], 'parent_span_id': match[2]},
                input={'customer_id': customer, 'text': masked}, metadata={'n8n_execution_id': execution})
            with root.start_as_current_observation(name='mask-pii', as_type='guardrail',
                    input={'checks': 'banking-regex-mask'}, output={'masked_categories': sorted(categories)}):
                pass
        run['root'] = root
        self.runs[execution] = run
        result = {'execution_id': execution, 'run_token': run['token'], 'text': masked,
                  'trace_id': run['trace_id'], 'pii_masked': run['masked']}
        run['results']['mask'] = result
        return result

    async def generate(self, run, stage):
        from northwind import config
        prompt = None
        if stage == 'classify':
            prompt = await asyncio.to_thread(self.adapter.langfuse().get_prompt, PROMPT_NAME,
                label='production', cache_ttl_seconds=0, max_retries=0, fetch_timeout_seconds=5)
            system, text, name = prompt.prompt, run['text'], 'classify-complaint'
        else:
            system, text, name = DRAFT, json.dumps(run['triage']) + '\n' + run['text'], 'draft-reply'
        model = self.adapter.native_llm()
        await asyncio.to_thread(self.adapter.check_spending)
        with self.attributes(run), run['root'].start_as_current_observation(name='complaint-step'), \
                propagate_attributes(metadata={'generation_owner': 'langchain'}):
            answer = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=text)],
                config={'run_name': name, 'callbacks': [CallbackHandler(public_key=os.environ.get('LANGFUSE_PUBLIC_KEY'))],
                        'metadata': {'langfuse_prompt': prompt} if prompt is not None else {}})
        content = answer.content if isinstance(answer.content, str) else ''.join(
            part.get('text', '') for part in answer.content if isinstance(part, dict))
        if stage == 'classify':
            triage = json.loads(content)
            if (not isinstance(triage, dict) or triage.get('category') not in {'card_fraud', 'fees', 'transfers', 'service', 'other'}
                    or triage.get('severity') not in {'low', 'medium', 'high'}
                    or type(triage.get('regulatory_escalation')) is not bool
                    or not isinstance(triage.get('rationale'), str) or len(triage['rationale']) > 300):
                raise ValueError('Invalid classification')
            run['triage'] = {key: triage[key] for key in ('category', 'severity', 'regulatory_escalation', 'rationale')}
            return {**run['results']['mask'], **run['triage']}
        result = {'execution_id': run['execution_id'], **run['triage'], 'reply': content,
            'trace_id': run['trace_id'], 'observation_id': run['root'].id,
            'trace_url': await asyncio.to_thread(config.trace_url, run['trace_id']),
            'n8n_execution_url': self.base_path + '/workflow/northwindComplaint/executions/' + run['execution_id'],
            'pii_masked': run['masked']}
        run['root'].update(output=result)
        run['root'].end()
        await asyncio.to_thread(self.adapter.langfuse().flush)
        return result

    def _expire(self):
        for key, run in list(self.runs.items()):
            if time.monotonic() - run['created'] > 600:
                if 'reply' not in run['results'] and not run.get('failed'):
                    run['root'].update(level='ERROR', status_message='ComplaintExecutionExpired')
                    run['root'].end()
                del self.runs[key]

    async def stop(self):
        for run in self.runs.values():
            if 'reply' not in run['results'] and not run.get('failed'):
                run['root'].update(level='ERROR', status_message='LiveInstanceStopped')
                run['root'].end()
        if self.server:
            self.server.should_exit = True
        if self.task:
            await self.task
        self.runs.clear()
