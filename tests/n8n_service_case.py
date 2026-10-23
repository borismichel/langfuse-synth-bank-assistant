"""Real pinned n8n proof, inside isolated image with no external network."""
import asyncio
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import httpx
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from synth.companion.n8n import N8nService


async def main():
    calls, native, carriers = [], [], []
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            assert self.headers.get('X-Northwind-Workflow') == 'test-only-scoped-token'
            if self.headers.get('transfer-encoding') == 'chunked':
                chunks = []
                while True:
                    size = int(self.rfile.readline().split(b';')[0], 16)
                    if not size:
                        self.rfile.readline()
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)
                raw = b''.join(chunks)
            else:
                raw = self.rfile.read(int(self.headers.get('content-length', '0')))
            if self.headers.get('content-encoding') == 'gzip':
                import gzip
                raw = gzip.decompress(raw)
            if self.path == '/otel':
                batch = ExportTraceServiceRequest.FromString(raw)
                native.extend(span for r in batch.resource_spans for scope in r.scope_spans for span in scope.spans)
                self.send_response(200); self.send_header('content-type', 'application/x-protobuf'); self.end_headers()
                return
            body = json.loads(raw)
            carriers.append(self.headers.get('traceparent'))
            calls.append(self.path)
            result = {'execution_id': body.get('execution_id'), 'run_token': 'fixture-token',
                      'text': 'Masked synthetic complaint', 'category': 'fees',
                      'reply': 'Your duplicate fee complaint is recorded for review.'}
            data = json.dumps(result).encode()
            self.send_response(200)
            self.send_header('content-type', 'application/json')
            self.send_header('content-length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    callback = ThreadingHTTPServer(('127.0.0.1', 0), Callback)
    thread = Thread(target=callback.serve_forever, daemon=True)
    thread.start()
    service = N8nService(enabled=True, base_path='/live/test/n8n',
                         bridge_url=f'http://127.0.0.1:{callback.server_port}', token='test-only-scoped-token', public_origin='http://localhost:3009')
    await service.start()
    try:
        assert service.ready, getattr(service, 'error', None)
        async with httpx.AsyncClient(trust_env=False) as client:
            public = await client.get(service.url + '/rest/workflows')
            assert public.status_code == 401
            authenticated = await client.get(service.url + '/rest/login', headers={'cookie': service.cookie})
            assert authenticated.status_code == 200
            workflows = await client.get(service.url + '/rest/workflows', headers={'cookie': service.cookie})
            assert workflows.json()['count'] == 1, workflows.text
            page = await client.get(service.url + '/')
            assert page.status_code == 200 and '/live/test/n8n/' in page.text
            result = await client.post(service.url + '/webhook/complaint-triage',
                json={'text': 'My card fee was charged twice.', 'customer_id': 'C-1001'}, timeout=20)
            assert result.status_code == 200, result.text
            assert result.json()['category'] == 'fees', result.text
            assert calls == ['/mask', '/classify', '/reply'], calls
        first_cookie, first_directory = service.cookie, service.directory.name
        assert os.getuid() == 10001
        assert service.env['N8N_LISTEN_ADDRESS'] == '127.0.0.1'
        assert not any(key in service.env for key in ('LANGFUSE_SECRET_KEY', 'OPENAI_API_KEY', 'PORTAL_LIVE_PROOF'))
    finally:
        await service.stop()
        callback.shutdown()
        thread.join()
    assert {'workflow.execute', 'node.execute'} <= {span.name for span in native}, [s.name for s in native]
    assert len({s.trace_id.hex() for s in native}) == 1
    assert all(h and h.split('-')[1] == native[0].trace_id.hex() for h in carriers), carriers
    assert service.process.returncode is not None and not Path(service.directory.name).exists()
    # A later instance gets empty state and cannot accept the previous owner session.
    other = N8nService(enabled=True, base_path='/live/other/n8n', bridge_url='http://127.0.0.1:1',
                       token='other-scoped-token', public_origin='http://localhost:3009')
    await other.start()
    try:
        assert other.ready, other.error
        assert other.directory.name != first_directory and other.cookie != first_cookie
        async with httpx.AsyncClient(trust_env=False) as client:
            stale = await client.get(other.url + '/rest/workflows', headers={'cookie': first_cookie})
            assert stale.status_code == 401
            history = await client.get(other.url + '/rest/executions', headers={'cookie': other.cookie})
            assert history.status_code == 200 and history.json()['data']['count'] == 0, history.text
        other.process.kill()
        await other.process.wait()
        await asyncio.sleep(.1)
        assert not other.ready and other.error == 'WorkflowProcessExited'
    finally:
        await other.stop()
    assert not Path(other.directory.name).exists()
    peak = Path('/sys/fs/cgroup/memory.peak')
    peak_bytes = int(peak.read_text()) if peak.exists() else None
    assert peak_bytes is None or peak_bytes < 2 * 1024 ** 3
    print(json.dumps({'peak_bytes': peak_bytes, 'new_instance_isolated': True, 'child_failure': True, 'authenticated_editor' : True, 'prefix': True, 'cleanup': True, 'actual_workflow': True, 'native_tracing': True}))

asyncio.run(main())
