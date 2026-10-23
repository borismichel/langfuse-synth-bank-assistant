"""Managed presenter controls. The generic Depot gate owns route authorization."""
import asyncio,json,os
from fastapi import HTTPException
from fastapi.responses import HTMLResponse,JSONResponse
from pydantic import BaseModel,Field
from langfuse_synth_core.live import paths
from langfuse_synth_core.companion.access import route_access,validate_protected_paths
from .judges import PolicyJudges


def error_detail(exc):
    # API bodies can contain provider diagnostics; do not reflect them or credentials.
    if isinstance(exc,ValueError):return str(exc)
    code=getattr(exc,'status_code',None)
    if code in (401,403):return f'Langfuse access denied (HTTP {code}); check project permissions'
    if code==429:return 'Langfuse rate limit reached; refresh status before retrying'
    if code:return f'Langfuse request failed (HTTP {code}); refresh status to confirm the outcome'
    return type(exc).__name__+'; refresh status to confirm the outcome'


class Activation(BaseModel):
    request_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    model:str=Field(min_length=1,max_length=120,pattern=r'^[A-Za-z0-9._:/-]+$')
    sampling:float=Field(gt=0,le=1)
    cost_acknowledged:bool=False


class Action(BaseModel):
    request_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')


class JudgeControls:
    def __init__(self,adapter,banking,preview):
        self.service=None if preview else PolicyJudges(adapter,banking.instance,
            os.environ.get('LANGFUSE_TRACING_ENVIRONMENT','production'))
        self.jobs={};self.operation={};self.lock=asyncio.Lock();self.closing=False

    def required(self):
        if self.service is None:raise HTTPException(503,'Policy judges unavailable in development preview')
        policy=validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS','[]')))
        if route_access('/manage/judges',policy)!='manage' or not os.environ.get('PORTAL_LIVE_PROOF'):
            raise HTTPException(503,'Managed presenter access is not configured')
        if self.closing:raise HTTPException(503,'Companion is stopping')
        return self.service

    def status(self):
        return {**self.required().snapshot(),'operation':self.operation}

    async def stop(self):
        self.closing=True
        if self.service:self.service.cancelled.set()
        jobs=[job['task'] for job in self.jobs.values() if not job['task'].done()]
        if jobs:await asyncio.gather(*jobs,return_exceptions=True)

    async def activate(self,body):
        service=self.required()
        if not body.cost_acknowledged:raise HTTPException(400,'Confirm the cost and remote-rule lifecycle before activation')
        payload=body.model_dump();prior=self.jobs.get(body.request_id)
        if prior:
            if prior['payload']!=payload:raise HTTPException(409,'Request identity has different settings')
            return JSONResponse(self.status(),status_code=202 if not prior['task'].done() else 200)
        if self.lock.locked():raise HTTPException(409,'A judge operation is already running')
        if len(self.jobs)>=32:raise HTTPException(429,'Judge operation limit reached for this process')
        # Acquire before scheduling so simultaneous starts cannot pass an unlocked check.
        await self.lock.acquire()
        self.operation={'request_id':body.request_id,'status':'running','action':'activate'}
        async def run():
            try:
                await asyncio.to_thread(service.activate,body.model,body.sampling)
                self.operation={**self.operation,'status':'succeeded'}
            except Exception as exc:
                detail=error_detail(exc)
                self.operation={**self.operation,'status':'failed','error':detail}
            finally:self.lock.release()
        task=asyncio.create_task(run())
        self.jobs[body.request_id]={'payload':payload,'task':task}
        return JSONResponse(self.status(),status_code=202)


def mount_judges(app,adapter,banking,preview=False):
    control=JudgeControls(adapter,banking,preview)
    @app.get('/manage/judges',response_class=HTMLResponse)
    async def page():
        service=control.required()
        prefix=paths.base_path()
        from .judge_view import judge_page
        return judge_page(service.provider,prefix)
    @app.get('/manage/judges/status')
    async def status(refresh:bool=False):
        service=control.required()
        if refresh:
            if control.lock.locked():raise HTTPException(409,'A judge operation is already running')
            async with control.lock:
                try:await asyncio.to_thread(service.refresh)
                except Exception as exc:
                    service.last_error=error_detail(exc)
                    if service.rule:service.rule['enabled']=None
        return control.status()
    @app.post('/manage/judges/activate')
    async def activate(body:Activation):return await control.activate(body)
    @app.post('/manage/judges/pause')
    async def pause(body:Action):
        service=control.required()
        if control.lock.locked():raise HTTPException(409,'Wait for the current judge operation before pausing')
        async with control.lock:
            try:return await asyncio.to_thread(service.pause)
            except Exception as exc:raise HTTPException(502,error_detail(exc)) from None
    return control
