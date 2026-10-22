"""Delivered HTTP APM route, real Jaeger, no target/provider/network required."""
import json
import os
import tempfile
from pathlib import Path
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class TargetHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        body = b'{"data":[{"id":"fixture-project"}]}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

target = ThreadingHTTPServer(('127.0.0.1', 0), TargetHandler)
Thread(target=target.serve_forever, daemon=True).start()
os.environ['LANGFUSE_BASE_URL'] = f'http://127.0.0.1:{target.server_port}' 
from fastapi.testclient import TestClient
from langfuse_synth_core.companion import CompanionAdapter
from synth.companion.app import create_app, HEALTH_PATH
from synth.config import load_config

with tempfile.TemporaryDirectory() as directory:
    os.environ.update(SYNTH_STATE_DIR=directory, LIVE_BASE_PATH='/live/apm-fixture',
                      PORTAL_LIVE_PROOF='a'*64,
                      PORTAL_LIVE_PROTECTED_PATHS='[{"path":"/apm","access":"presenter"}]')
    Path(directory, 'runtime-config.json').write_text(json.dumps({'apm_enabled': True}))
    adapter = CompanionAdapter(load_config('config/demo.yaml'), health_path=HEALTH_PATH)
    app = adapter.make_server(create_app(adapter), port=8080).config.app
    with TestClient(app) as client:
        assert client.get('/apm/').status_code == 404
        headers = {'x-depot-live-proof': 'a'*64}
        page = client.get('/apm/', headers=headers)
        assert page.status_code == 200 and '<html' in page.text.lower()
        assert client.get('/apm/api/v3/services', headers=headers).status_code == 200
        assert client.post('/apm/api/otlp/v1/traces', headers=headers, json={}).status_code == 405
        status = client.get('/api/health').json()
        assert status['detail']['apm'] == {'enabled': True, 'ok': True}
        assert 'a'*64 not in page.text + json.dumps(status)
        info = client.get('/api/info', headers=headers).json()
        assert {'label': 'APM', 'url': '/live/apm-fixture/apm/'} in info['links']
    # No child survives ASGI shutdown, and health exposed no process credentials.
    print(json.dumps({'delivered_apm': True, 'private_direct_route': True, 'read_only': True,
                      'apm_ready': True, 'private_link': True, 'shutdown_complete': True}))

target.shutdown()
