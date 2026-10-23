"""Owned application experiments, distinct from native prompt-only replay."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from fastapi.responses import JSONResponse
from synth.assets import fingerprint
from .experiment_runner import Replay, prepare_prompts, readback
from fastapi import HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from langfuse_synth_core.companion.access import route_access, validate_protected_paths
from langfuse_synth_core.live import paths
from synth.assets import state_path

class ExperimentRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    cohort: str = Field(pattern=r'^(english|spanish|redteam|disputes)$')
    baseline_version: int = Field(ge=1)
    candidate_version: int = Field(ge=1)
    candidate_policy: str = Field(default='current', pattern=r'^(current|short-lookback|broad-retrieval)$')
    judge_model: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9._:/-]+$')
    cost_acknowledged: bool = False


COHORTS = ('english', 'spanish', 'redteam', 'disputes')


class ExperimentControls:
    def __init__(self, adapter, banking, preview):
        self.adapter, self.banking, self.preview = adapter, banking, preview
        self.strategies = {}
        self.runs = []
        self.state_error = False
        self.closing = False
        self.job = None
        self.cancelled = threading.Event()
        self.lock = asyncio.Lock()
        key = fingerprint([getattr(adapter,'base_url',''),os.environ.get('LANGFUSE_PUBLIC_KEY'),getattr(banking,'instance','preview')])
        # Imported history is mounted read-only. Receipts share only this live container's lifetime.
        self.file = Path(tempfile.gettempdir()) / ('northwind-experiments-'+str(os.getuid())) / key / 'comparisons.json'
        try:
            saved=json.loads(self.file.read_text())
            if saved.get('version')!=1 or not isinstance(saved.get('runs'),list) or len(saved['runs'])>8:
                raise ValueError('Invalid comparison receipt')
            self.runs=saved['runs']
            for run in self.runs:
                if run['status']=='running':run.update(status='interrupted',error='Process stopped; inspect partial native results. A new request identity is required to rerun.')
        except FileNotFoundError:pass
        except (OSError,ValueError,KeyError,TypeError):self.state_error=True

    def persist(self):
        self.file.parent.parent.mkdir(mode=0o700, exist_ok=True)
        self.file.parent.mkdir(mode=0o700, exist_ok=True)
        temporary = self.file.with_suffix('.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'version':1,'runs':self.runs}, stream, sort_keys=True)
        temporary.replace(self.file)

    def required(self):
        if self.state_error:raise HTTPException(503,'Comparison receipt is unreadable; inspect native evidence before replacing this deployment')
        if self.preview or self.adapter is None:
            raise HTTPException(503, 'Application experiments are unavailable in preview')
        policy = validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS', '[]')))
        if route_access('/manage/experiments', policy) != 'manage' or not os.environ.get('PORTAL_LIVE_PROOF'):
            raise HTTPException(503, 'Managed presenter access is not configured')
        if self.closing:
            raise HTTPException(503, 'Companion is stopping')

    def status(self):
        self.required()
        try:
            receipt = json.loads(state_path('asset-receipt.json').read_text())
            cohorts = {key: {'expected': len(receipt['datasets'][key]['item_ids']),
                'snapshot_at': receipt['datasets'][key]['snapshot_at'],
                'cohort_hash': receipt['datasets'][key]['cohort_hash'],
                'url': receipt['datasets'][key]['url']} for key in COHORTS}
        except (OSError, ValueError, KeyError):
            cohorts = {}
        return {'scope':'application-retrieval-and-mcp', 'cohorts':cohorts, 'runs':self.runs,
            'cost_scope':'Application and local judge calls use Depot checks. Remote managed evaluations are separate and may continue after cancellation.',
            'reference_labels':'Constructed fixtures; not independent human reviews.'}

    async def start(self, body, *, kind='application'):
        strategy = self.strategies.get(kind)
        if kind != 'application' and strategy is None:raise HTTPException(409, 'Experiment kind is unavailable')
        self.required()
        if not body.cost_acknowledged:
            raise HTTPException(400, 'Confirm application and judge costs before starting')
        async with self.lock:
            payload=body.model_dump()
            prior=next((r for r in self.runs if r['request_id']==body.request_id),None)
            if prior:
                if prior.get('kind','application')!=kind or prior['request']!=payload:raise HTTPException(409,'Request identity has different settings')
                return JSONResponse(prior,status_code=202 if prior['status']=='running' else 200)
            if self.job and not self.job.done():raise HTTPException(409,'An experiment is already running')
            if len(self.runs)>=8:raise HTTPException(429,'This deployment reached its eight-comparison limit')
            if not self.banking.ready:raise HTTPException(503,'Banking service is unavailable')
            run=dict(id=uuid4().hex,kind=kind,request_id=body.request_id,request=payload,status='running',
                created_at=datetime.now(timezone.utc).isoformat(),dispatches=0,
                limits={'cases_per_arm':16,'arms':2,'application_dispatches_per_case':10,'judge_calls_per_case':5,
                        'total_dispatches':480,'deadline_seconds':600,'concurrency':1},
                arms={arm:dict(id=uuid4().hex,rows=[]) for arm in ('baseline','candidate')},
                gate={'status':'incomplete','expected':0,'scored':0,'missing':0,'errors':0,'failed':0})
            if strategy:
                initial=strategy.initial(body)
                run.update(initial,arms={arm:dict(id=uuid4().hex,rows=[]) for arm in initial['arms']})
            self.runs.append(run);self.persist();self.cancelled=threading.Event()
            replay=(strategy or Replay)(self,run,body,self.cancelled)
            async def execute():
                # Publish a terminal status only after the worker has flushed and stopped.
                status=await asyncio.to_thread(replay.execute)
                run['status']=status
                self.persist()
            self.job=asyncio.create_task(execute())
            return JSONResponse(run,status_code=202)

    async def prepare(self):
        self.required()
        async with self.lock:
            if self.job and not self.job.done():raise HTTPException(409,'An experiment is already running')
            try:return await asyncio.to_thread(prepare_prompts,self.adapter)
            except Exception as exc:
                raise HTTPException(409,str(exc) if isinstance(exc,ValueError) else type(exc).__name__) from None

    async def refresh(self,identity):
        self.required()
        async with self.lock:
            if self.job and not self.job.done():raise HTTPException(409,'Wait for execution to finish before native readback')
            run=next((r for r in self.runs if r['id']==identity),None)
            if run is None:raise HTTPException(404,'Comparison not found')
            if 'scope' not in run:raise HTTPException(409,'Comparison did not reach fixed cohort preparation')
            strategy=self.strategies.get(run.get('kind','application'))
            if run.get('kind','application')!='application' and strategy is None:
                raise HTTPException(409,'Experiment kind is unavailable')
            from .experiment_gate import assess
            for arm in run['arms'].values():
                for row in arm['rows']:row['verified']=False
            if strategy:strategy.reset_readback(run)
            else:run['gate']=assess(run['expected'],run['arms']['baseline']['rows'],run['arms']['candidate']['rows'],required=run['scope'].get('required_checks'))
            self.persist()
            try:
                result=await asyncio.to_thread(strategy.readback if strategy else readback,self.adapter,run)
                self.persist();return result
            except Exception as exc:
                raise HTTPException(409,str(exc) if isinstance(exc,ValueError) else type(exc).__name__) from None

    async def stop(self):
        self.closing = True
        self.cancelled.set()
        if self.job:await self.job
        async with self.lock:pass


def mount_experiments(app, adapter, banking, preview=False):
    controls = ExperimentControls(adapter, banking, preview)
    @app.get('/manage/experiments', response_class=HTMLResponse)
    async def page():
        controls.required()
        from html import escape
        prefix = escape(paths.base_path(), quote=True)
        from pathlib import Path
        return Path('demos/bank-assistant/portal/static/experiments.html').read_text().replace('__PREFIX__',prefix)
    @app.post('/manage/experiments/prepare')
    async def prepare(): return await controls.prepare()
    @app.post('/manage/experiments/{identity}/readback')
    async def refresh(identity: str): return await controls.refresh(identity)
    @app.post('/manage/experiments/cancel')
    async def cancel():
        controls.required();controls.cancelled.set()
        return {'status':'cancellation_requested','detail':'No new model calls will start. An in-flight call may finish and remain billable.'}
    @app.post('/manage/experiments/start')
    async def start(body: ExperimentRequest): return await controls.start(body)
    @app.get('/manage/experiments/status')
    async def status(): return controls.status()
    from .conduct_comparison import mount_conduct
    mount_conduct(app,controls)
    return controls
