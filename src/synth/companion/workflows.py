"""Managed workflow lifecycle and authenticated editor proxy."""
import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from langfuse_synth_core.companion.access import route_access, validate_protected_paths, validate_request_path
from langfuse_synth_core.live import paths
from .complaints import ComplaintBridge, PROMPT_NAME
from .n8n import N8nService


class WorkflowService:
    def __init__(self, adapter, *, enabled):
        self.adapter = adapter
        self.enabled = enabled
        self.runtime = None
        self.bridge = None
        self.error = None

    @property
    def ready(self):
        return bool(self.runtime and self.runtime.ready and self.bridge and not self.bridge.error)

    def detail(self):
        return {'enabled': self.enabled, 'ok': self.ready,
                'error': self.error or (self.bridge.error if self.bridge else None)
                or (self.runtime.error if self.runtime else None)}

    async def start(self):
        if not self.enabled:
            return
        try:
            policy = validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS', '[]')))
            if not route_access('/n8n', policy) or not os.environ.get('PORTAL_LIVE_PROOF'):
                self.error = 'WorkflowProtectionMissing'
                return
            origin = paths.public_origin()
            if not origin:
                self.error = 'WorkflowPublicOriginMissing'
                return
            await asyncio.to_thread(self.adapter.langfuse().get_prompt, PROMPT_NAME,
                label='production', max_retries=0, fetch_timeout_seconds=5)
            self.bridge = ComplaintBridge(self.adapter,
                instance_id=os.environ['PORTAL_LIVE_INSTANCE_ID'], base_path=paths.base_path() + '/n8n')
            await self.bridge.start()
            self.runtime = N8nService(enabled=True, base_path=paths.base_path() + '/n8n',
                bridge_url=self.bridge.url, token=self.bridge.token, public_origin=origin)
            await self.runtime.start()
            if not self.runtime.ready:
                self.error = self.runtime.error or 'WorkflowStartupFailed'
                await self.stop()
        except Exception as exc:
            self.error = type(exc).__name__
            await self.stop()

    async def stop(self):
        # Keep the local collector alive until n8n has flushed native spans.
        if self.runtime:
            await self.runtime.stop()
        if self.bridge:
            await self.bridge.stop()


def mount_workflow(app, service):
    @app.get('/n8n')
    async def root():
        if not service.enabled:
            raise HTTPException(404)
        return RedirectResponse(paths.base_path() + '/n8n/workflow/northwindComplaint')

    @app.api_route('/n8n/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def proxy(path: str, request: Request):
        if not service.enabled:
            raise HTTPException(404)
        if not service.ready:
            raise HTTPException(503, 'Complaint workflow unavailable')
        try:
            validate_request_path('/n8n/' + path)
        except ValueError:
            raise HTTPException(400, 'Invalid workflow path') from None
        runtime = service.runtime
        origin = request.headers.get('origin')
        if origin and origin != runtime.public_origin:
            raise HTTPException(403, 'Request origin does not match this deployment')
        # The per-instance owner session is transport, not a browser credential.
        # Account/bootstrap operations are not presenter actions.
        if path.startswith(('rest/owner', 'rest/users', 'rest/invitations')) or (
                request.method not in {'GET', 'HEAD'} and path in {'rest/login', 'rest/logout'}):
            raise HTTPException(404)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise HTTPException(413)
        public = urlsplit(runtime.public_origin)
        headers = {'cookie': runtime.cookie, 'accept-encoding': 'identity',
                   'origin': runtime.public_origin, 'x-forwarded-host': public.netloc,
                   'x-forwarded-proto': public.scheme}
        for key in ('content-type', 'accept', 'browser-id'):
            if key in request.headers:
                headers[key] = request.headers[key]
        client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(75, connect=3))
        url = runtime.url + '/' + path
        if request.url.query:
            url += '?' + request.url.query
        try:
            response = await client.send(client.build_request(request.method, url, headers=headers,
                content=bytes(body)), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            raise HTTPException(503, 'Complaint workflow unavailable') from None
        event_stream = response.headers.get('content-type', '').startswith('text/event-stream')
        async def stream():
            pending = None
            try:
                iterator = response.aiter_raw().__aiter__()
                while True:
                    pending = asyncio.create_task(anext(iterator))
                    while event_stream:
                        done, _ = await asyncio.wait({pending}, timeout=15)
                        if done:
                            break
                        # n8n pings at 60s; Depot's HTTP read window is 30s.
                        yield b':managed-workflow-keepalive\n\n'
                    try:
                        yield await pending
                    except StopAsyncIteration:
                        break
            finally:
                if pending and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                await response.aclose()
                await client.aclose()
        response_headers = {k: v for k, v in response.headers.items()
                            if k.lower() in {'content-type', 'etag', 'content-encoding'}}
        response_headers['cache-control'] = 'no-store'
        if event_stream:
            response_headers['x-accel-buffering'] = 'no'
        location = response.headers.get('location')
        if location and location.startswith('/'):
            response_headers['location'] = (location if location.startswith(runtime.base_path + '/')
                                            else runtime.base_path + location)
        return StreamingResponse(stream(), status_code=response.status_code, headers=response_headers)
