"""Managed presenter controls. The generic Depot gate owns route authorization."""
import asyncio,json,os
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse,JSONResponse
from pydantic import BaseModel,Field,ValidationError
from langfuse_synth_core.live import paths
from langfuse_synth_core.companion.access import route_access,validate_protected_paths
from .judges import PolicyJudges
from .reconciliation import Reconciler


def error_detail(exc):
    # API bodies can contain provider diagnostics; do not reflect them or credentials.
    if isinstance(exc,ValidationError):
        return 'Langfuse returned an invalid response; refresh status to confirm the outcome'
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
        self.reconciler=Reconciler(self.service) if self.service else None
        self.jobs={};self.operation={};self.lock=asyncio.Lock();self.closing=False
        self.background=None;self.wake=asyncio.Event();self.lease=None

    def required(self):
        if self.service is None:raise HTTPException(503,'Policy judges unavailable in development preview')
        policy=validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS','[]')))
        if route_access('/manage/judges',policy)!='manage' or not os.environ.get('PORTAL_LIVE_PROOF'):
            raise HTTPException(503,'Managed presenter access is not configured')
        if self.closing:raise HTTPException(503,'Companion is stopping')
        return self.service

    def status(self):
        return {**self.required().snapshot(),'operation':self.operation,'reconciliation':self.reconciler.snapshot()}

    async def start(self):
        if not self.service:return
        try:self.required()
        except HTTPException:
            self.reconciler.state.update(lifecycle='unavailable',status='protection_missing')
            return
        # Depot runs one Companion container. The lease also excludes another worker
        # for the same target/deployment inside that container or a local rehearsal.
        import fcntl,tempfile
        from pathlib import Path
        from synth.assets import fingerprint
        identity=[self.service.adapter.base_url,os.environ.get('LANGFUSE_PUBLIC_KEY'),self.service.instance]
        folder=Path(tempfile.gettempdir())/('northwind-reconciler-'+str(os.getuid()))
        folder.mkdir(mode=0o700,exist_ok=True)
        fd=os.open(folder/fingerprint(identity),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        self.lease=os.fdopen(fd,'w')
        try:fcntl.flock(self.lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close();self.lease=None
            self.reconciler.state.update(lifecycle='unavailable',status='another_owner')
            return
        self.reconciler.hold_path=folder/(fingerprint(identity)+'.hold')
        if self.reconciler.hold_path.exists():
            self.reconciler.halted=True
            self.reconciler.state.update(status='action_required',error='Previous write needs native inspection before Retry reconciliation.')
        self.reconciler.state['lifecycle']='running'
        async def loop():
            while not self.closing:
                try:await asyncio.wait_for(self.wake.wait(),timeout=30)
                except TimeoutError:pass
                if self.closing:break
                async with self.lock:
                    if self.closing:break
                    await self.reconcile()
        self.background=asyncio.create_task(loop())

    async def reconcile(self, *, retry=False):
        try:await asyncio.to_thread(self.reconciler.run,retry=retry)
        except Exception as exc:
            self.reconciler.state.update(status='error',error=error_detail(exc))

    async def stop(self):
        self.closing=True;self.wake.set()
        if self.service:self.service.cancelled.set()
        jobs=[job['task'] for job in self.jobs.values() if not job['task'].done()]
        if self.background:jobs.append(self.background)
        if jobs:await asyncio.gather(*jobs,return_exceptions=True)
        async with self.lock:pass  # Drain a presenter-triggered reconciliation before SDK shutdown.
        if self.lease:self.lease.close();self.lease=None
        if self.reconciler:self.reconciler.state['lifecycle']='stopped'

    async def activate(self,body):
        service=self.required()
        if not body.cost_acknowledged:raise HTTPException(400,'Confirm the cost and remote-rule lifecycle before activation')
        payload=body.model_dump();prior=self.jobs.get(body.request_id)
        if prior:
            if prior['payload']!=payload:raise HTTPException(409,'Request identity has different settings')
            return JSONResponse(jsonable_encoder(self.status()),status_code=202 if not prior['task'].done() else 200)
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
        return JSONResponse(jsonable_encoder(self.status()),status_code=202)


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
    @app.post('/manage/judges/reconcile')
    async def reconcile(body:Action):
        control.required()
        if control.lock.locked():raise HTTPException(409,'A judge operation is already running')
        if control.lease is None:raise HTTPException(409,'This Companion does not own reconciliation')
        async with control.lock:
            await control.reconcile(retry=True)
        return control.status()
    @app.post('/manage/judges/pause')
    async def pause(body:Action):
        service=control.required()
        if control.lock.locked():raise HTTPException(409,'Wait for the current judge operation before pausing')
        async with control.lock:
            try:return await asyncio.to_thread(service.pause)
            except Exception as exc:raise HTTPException(502,error_detail(exc)) from None
    return control
