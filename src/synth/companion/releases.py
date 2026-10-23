"""Prompt label changes authorised by owned, freshly verified experiment evidence."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from langfuse_synth_core.companion.access import route_access, validate_protected_paths
from langfuse_synth_core.live import paths
from synth.assets import fingerprint
from .experiment_gate import assess
from .experiment_runner import exact_prompt, freeze, readback
from .experiments import ExperimentRequest


class Promote(BaseModel):
    request_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    comparison_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class Rollback(BaseModel):
    request_id: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    release_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class Releases:
    def __init__(self, experiments):
        self.experiments = experiments
        self.adapter = experiments.adapter
        self.file = experiments.file.with_name('releases.json')
        self.records = []
        self.error = False
        self.job = None
        self.operation = None
        self.closing = False
        try:
            saved = json.loads(self.file.read_text())
            if saved.get('version') != 1 or not isinstance(saved.get('records'), list) or len(saved['records']) > 16:
                raise ValueError('Invalid release receipt')
            self.records = saved['records']
            for record in self.records:
                if record['status'] == 'changing':
                    record.update(status='unconfirmed', detail='Process stopped during label change. Inspect native prompt labels before another action.')
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            self.error = True

    def required(self):
        if self.closing:
            raise HTTPException(503, 'Prompt release controls are stopping')
        self.experiments.required()
        policy = validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS', '[]')))
        if route_access('/manage/releases', policy) != 'manage':
            raise HTTPException(503, 'Managed release access is not configured')
        if self.error:
            raise HTTPException(503, 'Release receipt is unreadable. Inspect native prompt history before replacing this instance.')

    def persist(self):
        self.file.parent.parent.mkdir(mode=0o700, exist_ok=True)
        self.file.parent.mkdir(mode=0o700, exist_ok=True)
        temporary = self.file.with_suffix('.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'version':1, 'records':self.records}, stream, sort_keys=True)
        temporary.replace(self.file)

    def status(self):
        self.required()
        result = {'records':self.records, 'comparisons':[
            {'id':r['id'], 'status':r['status'], 'gate':r['gate'], 'cohort':r['request']['cohort'],
             'candidate_policy':r['request']['candidate_policy'],
             'candidate':r.get('scope', {}).get('prompts', {}).get('candidate'),
             'production_at_start':r.get('scope', {}).get('production_at_start')}
            for r in self.experiments.runs if r.get('kind', 'application')=='application'],
            'scope':'Each decision covers its recorded fixed cohort. It does not qualify the full kit.',
            'concurrency':'Production is checked before and after changes. The target API has no atomic compare-and-swap; coordinate external label edits.'}
        try:
            result['production'] = self.prompt('production')
        except Exception:
            result['production'] = None
        result['records'] = [{**record, 'rollback_eligible':bool(record.get('kind')=='promote'
            and record['status']=='promoted' and record['new']==result['production'])} for record in self.records]
        for comparison in result['comparisons']:
            comparison['eligible'] = bool(comparison['status']=='completed' and comparison['gate']['status']=='passed'
                and comparison['candidate_policy']=='current' and comparison['candidate']
                and comparison['production_at_start']==result['production']
                and comparison['candidate']!=result['production'])
        if self.experiments.runs:
            from urllib.parse import quote
            project = next((r.get('scope', {}).get('project_id') for r in self.experiments.runs
                            if r.get('scope', {}).get('project_id')), None)
            if project:
                from northwind.prompts import PROMPT_NAME
                result['prompt_url'] = self.adapter.base_url+'/project/'+quote(project, safe='')+'/prompts/'+quote(PROMPT_NAME, safe='')
        return result

    def validate(self, run):
        if run.get('kind', 'application') != 'application':
            raise ValueError('Only application comparisons can authorise a prompt release')
        if run['status'] != 'completed' or run['gate']['status'] != 'passed':
            raise ValueError('Select a completed comparison with a passed gate')
        if run['request']['candidate_policy'] != 'current':
            raise ValueError('The tested retrieval policy differs from the live application')
        for arm in run['arms'].values():
            for row in arm['rows']:
                row['verified'] = False
        run['gate'] = assess(run['expected'], run['arms']['baseline']['rows'], run['arms']['candidate']['rows'],
                             required=run['scope'].get('required_checks'))
        self.experiments.persist()
        readback(self.adapter, run)
        self.experiments.persist()
        if run['gate']['status'] != 'passed':
            raise ValueError('Native experiment evidence is incomplete or failed')
        _, _, _, scope = freeze(self.adapter, ExperimentRequest(**run['request']))
        if scope != run['scope']:
            raise ValueError('Production, cohort, rubric or model policy changed after this comparison')
        old, new = scope['production_at_start'], scope['prompts']['candidate']
        if old['version'] == new['version']:
            raise ValueError('The tested candidate is already in production')
        return old, new

    def prompt(self, label):
        from northwind.prompts import PROMPT_NAME
        prompt = self.adapter.langfuse().get_prompt(PROMPT_NAME, label=label,
            cache_ttl_seconds=0, max_retries=0, fetch_timeout_seconds=5)
        if prompt.is_fallback:
            raise ValueError('Native prompt readback is unavailable')
        return {'name':prompt.name, 'version':prompt.version, 'content_hash':fingerprint(prompt.prompt)}

    def move(self, record):
        client = self.adapter.langfuse()
        try:
            if self.prompt('production') != record['old']:
                raise ValueError('Production changed before the label update')
            client.api.prompt_version.update(name=record['new']['name'], version=record['new']['version'],
                new_labels=['production'], request_options={'max_retries':0, 'timeout_in_seconds':5})
            if self.prompt('production') != record['new']:
                raise ValueError('Production readback differs from the requested candidate')
            client.clear_prompt_cache()
            client.api.prompt_version.update(name=record['old']['name'], version=record['old']['version'],
                new_labels=['previous-production'], request_options={'max_retries':0, 'timeout_in_seconds':5})
            if self.prompt('previous-production') != record['old'] or self.prompt('production') != record['new']:
                raise ValueError('Prompt labels changed during readback')
            record.update(status='rolled_back' if record['kind']=='rollback' else 'promoted',
                          verified_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            # A failed response cannot establish that the mutation never happened.
            record.update(status='unconfirmed', detail='Label change was not confirmed. Inspect native prompt history; this request will not be repeated automatically.')
            try:
                observed = self.prompt('production')
                record['observed_production'] = observed
                if observed == record['old'] and getattr(exc, 'status_code', None) in (401, 403):
                    record.update(status='denied', detail='The target denied the label change.')
            except Exception:
                pass
            raise
        finally:
            client.clear_prompt_cache()
            self.persist()

    async def perform(self, kind, body):
        self.required()
        operation=(kind,body.model_dump())
        if self.job is not None and not self.job.done() and self.operation==operation:
            return await asyncio.shield(self.job)
        if self.job is not None and not self.job.done():
            raise HTTPException(409, 'A prompt release action is running')
        self.operation=operation
        self.job=asyncio.create_task(self._promote(body) if kind=='promote' else self._rollback(body))
        # A disconnected HTTP caller does not release the experiment lock while
        # the native thread still mutates. Retain and drain this owned task.
        self.job.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return await asyncio.shield(self.job)

    async def stop(self):
        self.closing=True
        if self.job is not None:
            await asyncio.shield(asyncio.gather(self.job,return_exceptions=True))

    async def promote(self, body):
        return await self.perform('promote',body)

    async def rollback(self, body):
        return await self.perform('rollback',body)

    async def _promote(self, body):
        self.required()
        async with self.experiments.lock:
            if self.experiments.job and not self.experiments.job.done():
                raise HTTPException(409, 'Wait for the current experiment to finish')
            payload = body.model_dump()
            prior = next((r for r in self.records if r['request_id']==body.request_id), None)
            if prior:
                if prior['request'] != payload:
                    raise HTTPException(409, 'Request identity has different settings')
                return prior
            if len(self.records) >= 16:
                raise HTTPException(429, 'This instance reached its 16-release limit')
            run = next((r for r in self.experiments.runs if r['id']==body.comparison_id), None)
            if run is None:
                raise HTTPException(404, 'Comparison not found in this instance')
            try:
                old, new = await asyncio.to_thread(self.validate, run)
            except Exception as exc:
                raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else 'Native release evidence is unavailable') from None
            record = {'id':uuid4().hex, 'kind':'promote', 'request_id':body.request_id, 'request':payload,
                'comparison_id':run['id'], 'cohort':run['request']['cohort'], 'gate':copy.deepcopy(run['gate']),
                'evidence':{k:v['url'] for k,v in run['arms'].items()}, 'scope_hash':fingerprint(run['scope']),
                'old':old, 'new':new, 'status':'changing', 'created_at':datetime.now(timezone.utc).isoformat()}
            self.records.append(record)
            self.persist()
            try:
                await asyncio.to_thread(self.move, record)
            except Exception:
                raise HTTPException(403 if record['status']=='denied' else 502, record['detail']) from None
            return record

    def validate_rollback(self, source):
        if source.get('kind') != 'promote' or source['status'] != 'promoted':
            raise ValueError('Select a confirmed promotion from this instance')
        if self.prompt('production') != source['new']:
            raise ValueError('Production changed after this promotion')
        previous = exact_prompt(self.adapter.langfuse(), source['old']['name'], source['old']['version'])
        if fingerprint(previous.prompt) != source['old']['content_hash']:
            raise ValueError('The recorded previous prompt content changed')

    async def _rollback(self, body):
        self.required()
        async with self.experiments.lock:
            if self.experiments.job and not self.experiments.job.done():
                raise HTTPException(409, 'Wait for the current experiment to finish')
            payload = body.model_dump()
            prior = next((r for r in self.records if r['request_id']==body.request_id), None)
            if prior:
                if prior['request'] != payload:
                    raise HTTPException(409, 'Request identity has different settings')
                return prior
            if len(self.records) >= 16:
                raise HTTPException(429, 'This instance reached its 16-release limit')
            source = next((r for r in self.records if r['id']==body.release_id), None)
            if source is None:
                raise HTTPException(404, 'Release not found in this instance')
            try:
                await asyncio.to_thread(self.validate_rollback, source)
            except Exception as exc:
                raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else 'Native rollback evidence is unavailable') from None
            record = {'id':uuid4().hex, 'kind':'rollback', 'request_id':body.request_id, 'request':payload,
                'release_id':source['id'], 'comparison_id':source['comparison_id'], 'cohort':source['cohort'],
                'gate':copy.deepcopy(source['gate']), 'evidence':source['evidence'], 'scope_hash':source['scope_hash'],
                'old':source['new'], 'new':source['old'], 'status':'changing',
                'created_at':datetime.now(timezone.utc).isoformat()}
            self.records.append(record)
            self.persist()
            try:
                await asyncio.to_thread(self.move, record)
            except Exception:
                raise HTTPException(403 if record['status']=='denied' else 502, record['detail']) from None
            return record


def mount_releases(app, experiments):
    controls = Releases(experiments)

    @app.get('/manage/releases', response_class=HTMLResponse)
    async def page():
        controls.required()
        from html import escape
        from pathlib import Path
        prefix = paths.base_path()
        return Path('demos/bank-assistant/portal/static/releases.html').read_text().replace(
            '__PREFIX__', escape(prefix, quote=True)).replace('__PREFIX_JSON__', json.dumps(prefix).replace('<', '\\u003c'))

    @app.get('/manage/releases/status')
    async def status():
        return await asyncio.to_thread(controls.status)

    @app.post('/manage/releases/promote')
    async def promote(body: Promote):
        return await controls.promote(body)

    @app.post('/manage/releases/rollback')
    async def rollback(body: Rollback):
        return await controls.rollback(body)

    return controls
