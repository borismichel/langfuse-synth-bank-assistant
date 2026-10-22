"""Private read-only Jaeger route; no portal credentials forwarded to the child."""
import json
from pathlib import Path

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from langfuse_synth_core.live import paths
from synth.receipt import RunReceipt


def apm_enabled():
    path = Path(RunReceipt.state_dir()) / 'runtime-config.json'
    if not path.exists():
        return False
    if path.stat().st_size > 4096:
        raise ValueError('Invalid runtime configuration')
    config = json.loads(path.read_text())
    if not isinstance(config, dict) or set(config) != {'apm_enabled'} or type(config['apm_enabled']) is not bool:
        raise ValueError('Invalid runtime configuration')
    return config['apm_enabled']


def mount_apm(app, service):
    @app.get('/apm')
    async def root():
        if not service.enabled:
            raise HTTPException(404)
        return RedirectResponse(paths.base_path() + '/apm/')

    @app.get('/apm/{path:path}')
    async def proxy(path: str, request: Request):
        if not service.enabled:
            raise HTTPException(404)
        if not service.ready:
            raise HTTPException(503, 'APM unavailable')
        from langfuse_synth_core.companion.access import validate_request_path
        try:
            validate_request_path('/apm/' + path)
        except ValueError as exc:
            raise HTTPException(400, 'Invalid APM path') from exc
        client = httpx.AsyncClient(trust_env=False, timeout=10)
        try:
            # Fixed loopback target, GET only. No inbound headers, cookies, proof,
            # tracing context or authorization reach Jaeger or its access logs.
            url = service.query_url + '/apm/' + path
            if request.url.query:
                url += '?' + request.url.query
            response = await client.send(client.build_request('GET', url), stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(503, 'APM unavailable') from exc
        async def body():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() in {'content-type', 'content-encoding', 'etag'}}
        headers['cache-control'] = 'no-store'
        return StreamingResponse(body(), status_code=response.status_code, headers=headers)
