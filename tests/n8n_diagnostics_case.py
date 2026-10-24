"""External n8n failures through the delivered CLI, health endpoint and logs.

The fake executable is an external dependency fixture. The Companion, runtime
configuration, MCP process and native target client are real. No paid calls.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4


class Target(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        body = {'version': '4.68.0', 'status': 'OK'} if self.path.endswith('/health') else {
            'data': [{'id': 'diagnostic-project', 'name': 'Diagnostic fixture', 'retentionDays': None}]}
        if '/prompts/' in self.path:
            from urllib.parse import unquote, urlsplit
            body = {'name': unquote(urlsplit(self.path).path.rsplit('/', 1)[-1]), 'version': 1,
                    'type': 'text', 'prompt': 'Fixture banking prompt.', 'labels': ['production'],
                    'tags': [], 'config': {}}
        raw = json.dumps(body).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        # Only the native SDK readiness ping is expected; no application writes.
        assert self.path == '/api/public/otel/v1/traces'
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')


FAKE = r'''
import json, os, pathlib, sys, time, threading, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
root = pathlib.Path(__file__).parent
stage = sys.argv[1]
secrets = [os.environ['N8N_ENCRYPTION_KEY'], os.environ['N8N_RUNNERS_AUTH_TOKEN']]
if stage == 'import:credentials' and (root/'mode').read_text() == 'import':
    data = json.loads(pathlib.Path(sys.argv[2].split('=', 1)[1]).read_text())
    secrets.append(data[0]['data']['value'])
    (root/'fixture-secrets.json').write_text(json.dumps(secrets))
    (root/'fixture-pids.json').write_text(json.dumps([os.getpid()]))
    print('Credential database could not be opened', file=sys.stderr, flush=True)
    for value in secrets: print('received '+value, file=sys.stderr, flush=True)
    # A credential split across separate writes must still be redacted whole.
    sys.stderr.write(secrets[0][:20]); sys.stderr.flush(); time.sleep(.05)
    sys.stderr.write(secrets[0][20:] + '\n'); sys.stderr.flush()
    print('Authorization: Bearer fixture-unknown-secret', file=sys.stderr, flush=True)
    print('password="fixture spaced secret" more payload', file=sys.stderr, flush=True)
    sys.stderr.write('x' * 200000 + '\n')
    for index in range(5000): print('Repeated diagnostic '+str(index), file=sys.stderr)
    sys.stderr.flush()
    sys.exit(17)
if stage != 'start': sys.exit(0)
(root/'fixture-secrets.json').write_text(json.dumps(secrets))
(root/'fixture-pids.json').write_text(json.dumps([os.getpid()]))
class Runtime(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers()
    def do_POST(self):
        owner = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        secrets.extend([owner['email'], owner['password'], 'fixture-runtime-cookie'])
        (root/'fixture-secrets.json').write_text(json.dumps(secrets))
        for value in secrets[:-1]: print('received '+value, file=sys.stderr, flush=True)
        print('cookie=fixture-runtime-cookie', file=sys.stderr, flush=True)
        self.send_response(200); self.send_header('Set-Cookie', 'n8n-auth=fixture-runtime-cookie')
        self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')
server = ThreadingHTTPServer(('127.0.0.1', int(os.environ['N8N_PORT'])), Runtime)
threading.Thread(target=server.serve_forever, daemon=True).start()
while not (root/'crash').exists(): time.sleep(.02)
print('Workflow database connection lost', file=sys.stderr, flush=True)
print('Authorization: Bearer fixture-unknown-secret', file=sys.stderr, flush=True)
print('password="fixture spaced secret" more payload', file=sys.stderr, flush=True)
time.sleep(.1)
sys.stdout.write('x' * 200000 + '\n')
for index in range(5000): print('Repeated diagnostic '+str(index))
sys.stdout.flush()
if (root/'mode').read_text() == 'runtime-held-pipe':
    descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    (root/'fixture-pids.json').write_text(json.dumps([os.getpid(), descendant.pid]))
os._exit(23)
'''


def run():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'import'
    server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='n8n-diagnostics-') as temporary:
            root = Path(temporary); binary = root/'n8n'
            (root/'mode').write_text(mode)
            binary.write_text('#!' + sys.executable + '\n' + FAKE); binary.chmod(0o755)
            instance = str(uuid4())
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'PYTHONPATH')}
            env.update(PATH=str(root)+os.pathsep+env.get('PATH', ''),
                LANGFUSE_BASE_URL=f'http://127.0.0.1:{server.server_port}', LANGFUSE_PUBLIC_KEY='pk-fixture',
                LANGFUSE_SECRET_KEY='sk-fixture', LLM_PROVIDER='anthropic', LLM_MODEL='claude-sonnet-4-6',
                LLM_API_KEY='fixture-chat', ANTHROPIC_API_KEY='fixture-chat',
                SYNTH_STATE_DIR=str(root/'state'), SYNTH_OUT_DIR=str(root/'out'),
                PORTAL_LIVE_INSTANCE_ID=instance, PORTAL_LIVE_PROOF='a'*64,
                LIVE_BASE_PATH='/live/'+instance, PORTAL_LIVE_PUBLIC_ORIGIN='http://localhost:3009',
                PORTAL_LIVE_PROTECTED_PATHS=json.dumps([{'path': '/n8n', 'access': 'presenter'},
                                                       {'path': '/manage', 'access': 'manage'}]))
            cli = [sys.executable, '-m', 'synth.cli']
            seeded = subprocess.run(cli+['generate-spool', '--config', 'config/demo.yaml', '--set',
                'generation.target_traces=58', '--set', 'generation.as_of_date=2026-10-23',
                '--set', 'runtime.n8n_enabled=true'], env=env, capture_output=True, text=True, timeout=30)
            assert seeded.returncode == 0, 'Fixture history generation failed'
            with socket.socket() as available:
                available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
            with (root/'companion.log').open('w+') as output:
                child = subprocess.Popen(cli+['companion', '--config', 'config/demo.yaml', '--host',
                    '127.0.0.1', '--port', str(port)], env=env, stdout=output, stderr=output,
                    start_new_session=True)
                try:
                    deadline = time.monotonic()+25; health = None
                    while time.monotonic() < deadline and child.poll() is None:
                        try:
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
                                health = json.load(response)
                            workflow = health.get('detail', {}).get('workflow', {})
                            if mode.startswith('runtime') and workflow.get('ok'): (root/'crash').touch()
                            if workflow.get('error'): break
                        except urllib.error.HTTPError as failure:
                            health = json.load(failure)
                            if health.get('detail', {}).get('workflow', {}).get('error'): break
                        except (OSError, ValueError): pass
                        time.sleep(.1)
                    assert health and health['detail']['workflow']['error'], 'Failure did not reach health within bound'
                    assert not health['detail']['workflow']['ok']
                finally:
                    if child.poll() is None: child.send_signal(signal.SIGTERM)
                    try: child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
                        raise AssertionError('Companion did not stop within bound')
                output.seek(0); logs = output.read()
            # Assertions deliberately do not print captured secrets or raw child logs.
            if mode == 'import':
                assert 'Credential database could not be opened' in logs, 'Import failure diagnostic was discarded'
                assert 'stage=import:credentials' in logs and 'exit=17' in logs, 'Failure stage/exit missing'
            else:
                assert (root/'crash').exists(), 'Runtime never became ready before failure'
                assert health['detail']['workflow']['error'] == 'WorkflowProcessExited'
                assert 'Workflow database connection lost' in logs, 'Runtime failure diagnostic was discarded'
                assert 'stage=runtime' in logs and 'exit=23' in logs, 'Runtime failure stage/exit missing'
            assert 'diagnostic omitted' in logs, 'Oversized/flooded diagnostics were not bounded'
            assert len(logs.encode()) < 50000, 'Child output exceeded log budget'
            sensitive = json.loads((root/'fixture-secrets.json').read_text())
            sensitive += ['fixture-unknown-secret', 'fixture spaced secret']
            assert all(value not in logs for value in sensitive), 'Child diagnostic disclosed a credential'
            for pid in json.loads((root/'fixture-pids.json').read_text()):
                deadline = time.monotonic()+3
                while time.monotonic() < deadline:
                    result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
                    if not result.stdout.strip() or result.stdout.strip().startswith('Z'): break
                    time.sleep(.05)
                else: raise AssertionError('External workflow child survived shutdown')
            assert 'Task was destroyed' not in logs and 'unclosed transport' not in logs
            print(json.dumps({'mode': mode, 'bounded_bytes': len(logs.encode()),
                              'diagnostic_forwarded': True, 'secrets_absent': True, 'child_stopped': True}))
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__': run()
