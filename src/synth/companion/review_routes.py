"""Protected presenter review actions; human labels stay in native Langfuse."""
import asyncio,json,os
from fastapi import HTTPException
from fastapi.responses import HTMLResponse,JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel,Field
from langfuse_synth_core.companion.access import route_access,validate_protected_paths
from langfuse_synth_core.live import paths
from .judge_routes import error_detail as judge_error_detail

def error_detail(exc):
    from pydantic import ValidationError
    if isinstance(exc,ValidationError):return 'Target response does not match the supported API schema'
    return judge_error_detail(exc)
from .reviews import Reviews


class Prepare(BaseModel):
    request_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    pending:int=Field(default=10,ge=1,le=20)


class CalibrationPlan(BaseModel):
    request_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    model:str=Field(min_length=1,max_length=120,pattern=r'^[A-Za-z0-9._:/-]+$')
    include_reviews:bool=True


class Start(BaseModel):
    request_id:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    snapshot_hash:str=Field(min_length=64,max_length=64)
    cost_acknowledged:bool=False


class ReviewControls:
    def __init__(self,adapter,banking,preview):
        self.service=None if preview else Reviews(adapter,banking.instance)
        self.lock=asyncio.Lock();self.closing=False;self.task=None
        from .calibration import Calibration
        self.calibration=Calibration(self.service) if self.service else None
        if self.service:self.service.calibration_runner=self.calibration

    def required(self):
        policy=validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS','[]')))
        if not self.service or self.closing or route_access('/manage/reviews',policy)!='manage' or not os.environ.get('PORTAL_LIVE_PROOF'):
            raise HTTPException(503,'Managed review access is not configured')
        return self.service

    async def stop(self):
        self.closing=True
        if self.service:self.service.subjects.cancelled.set()
        if self.calibration:self.calibration.cancelled.set()
        if self.task:await self.task
        async with self.lock:pass


def mount_reviews(app,adapter,banking,preview=False):
    controls=ReviewControls(adapter,banking,preview)
    @app.get('/manage/reviews',response_class=HTMLResponse)
    async def page():
        controls.required()
        from .review_view import review_page
        return review_page(paths.base_path())
    @app.get('/manage/reviews/status')
    async def status():
        service=controls.required()
        if controls.lock.locked():return service.state
        async with controls.lock:
            try:return await asyncio.to_thread(service.refresh)
            except Exception as exc:raise HTTPException(502,error_detail(exc)) from None
    @app.post('/manage/reviews/prepare')
    async def prepare(body:Prepare):
        service=controls.required()
        if controls.lock.locked():raise HTTPException(409,'A review operation is running')
        async with controls.lock:
            try:
                await asyncio.to_thread(service.bindings)
                controls.calibration.claim()
                try:return await asyncio.to_thread(service.prepare,body.pending)
                finally:
                    controls.calibration.lease.close();controls.calibration.lease=None
            except Exception as exc:raise HTTPException(502,error_detail(exc)) from None
    @app.post('/manage/reviews/plan')
    async def plan(body:CalibrationPlan):
        service=controls.required()
        if controls.lock.locked():raise HTTPException(409,'A review operation is running')
        async with controls.lock:
            try:
                plan=await asyncio.to_thread(service.plan,body.request_id,body.model,body.include_reviews)
                controls.calibration.claim()
                try:service.state['calibration']=controls.calibration.remember(plan)
                finally:
                    controls.calibration.lease.close();controls.calibration.lease=None
                return service.state
            except Exception as exc:raise HTTPException(502,error_detail(exc)) from None
    @app.post('/manage/reviews/start')
    async def start(body:Start):
        service=controls.required();plan=service.state.get('calibration')
        if not body.cost_acknowledged:raise HTTPException(400,'Confirm the model and cost scope before calibration')
        if not plan or plan['request_id']!=body.request_id or plan['snapshot_hash']!=body.snapshot_hash:
            raise HTTPException(409,'Prepare and inspect this calibration snapshot before starting')
        if plan['status']!='planned':return JSONResponse(jsonable_encoder(service.state),status_code=200)
        if controls.lock.locked():raise HTTPException(409,'A review operation is running')
        try:controls.calibration.claim()
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        persisted=json.loads(controls.calibration.path(plan['request_id']).read_text())
        if any(persisted[k]!=plan[k] for k in ('snapshot_hash','model','provider')):
            controls.calibration.lease.close();controls.calibration.lease=None
            raise HTTPException(409,'Calibration identity changed; prepare a new action')
        if persisted['status']!='planned':
            service.state['calibration']=persisted
            controls.calibration.lease.close();controls.calibration.lease=None
            return service.state
        await controls.lock.acquire()
        controls.calibration.cancelled.clear()
        plan.update(status='running',completed=0,model_calls=0)
        controls.calibration.save(plan)
        async def run():
            try:await asyncio.to_thread(controls.calibration.run,plan)
            finally:controls.lock.release()
        controls.task=asyncio.create_task(run())
        return JSONResponse(jsonable_encoder(service.state),status_code=202)
    @app.post('/manage/reviews/readback')
    async def readback():
        service=controls.required()
        if controls.lock.locked():raise HTTPException(409,'Calibration is running')
        async with controls.lock:
            try:
                await asyncio.to_thread(controls.calibration.readback,service.state['calibration'])
                return service.state
            except Exception as exc:raise HTTPException(502,error_detail(exc)) from None
    @app.post('/manage/reviews/cancel')
    async def cancel():
        service=controls.required();controls.calibration.cancelled.set()
        return service.state
    return controls
