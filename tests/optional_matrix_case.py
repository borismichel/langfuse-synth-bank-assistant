"""All declared options through the real Companion command in a bounded container.

Run with network disabled; target API is a local HTTP fixture and keys are fake.
No inference, seed import, database access or application-internal replacement.
"""
import gzip
import itertools
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

writes = []
pings = []
class Target(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        body = {'version':'4.68.0','status':'OK'} if self.path.endswith('/health') else {
            'data':[{'id':'matrix-project','name':'Optional matrix demo','retentionDays':None}]}
        if '/prompts/' in self.path:
            from urllib.parse import urlsplit,unquote
            name=unquote(urlsplit(self.path).path.rsplit('/',1)[-1])
            body={'name':name,'version':1,'type':'text','prompt':'Fixture banking prompt.',
                  'labels':['production'],'tags':[],'config':{}}
        raw = json.dumps(body).encode()
        self.send_response(200); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        body=self.rfile.read(int(self.headers.get('Content-Length',0)))
        if self.headers.get('Content-Encoding')=='gzip':body=gzip.decompress(body)
        if self.path=='/api/public/otel/v1/traces':
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
            if 'json' in self.headers.get('Content-Type',''):
                from google.protobuf.json_format import Parse
                request=Parse(body.decode(),ExportTraceServiceRequest())
            else:
                request=ExportTraceServiceRequest.FromString(body)
            spans=[span for resource in request.resource_spans for scope in resource.scope_spans for span in scope.spans]
            if not spans:
                pings.append(self.path)
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}');return
        writes.append(self.path)
        self.send_response(503);self.send_header('Content-Length','0');self.end_headers()


def get(url, proof=None):
    request = urllib.request.Request(url, headers={'X-Depot-Live-Proof':proof} if proof else {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def processes():
    result = set()
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                if 'State:\tZ' not in (entry/'status').read_text(): result.add(int(entry.name))
            except FileNotFoundError: pass
    return result


def cgroup(name):
    path=Path('/sys/fs/cgroup')/name
    return path.read_text().strip() if path.exists() else None


assert os.getuid()==10001, 'Use the actual non-root image identity'
server=ThreadingHTTPServer(('127.0.0.1',0),Target)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
base=f'http://127.0.0.1:{server.server_port}'
results=[]
try:
    for comparison,voice,workflow,apm in itertools.product((False,True),repeat=4):
        flags=dict(comparison_enabled=comparison,voice_enabled=voice,n8n_enabled=workflow,apm_enabled=apm)
        before=processes()
        with tempfile.TemporaryDirectory(prefix='option-matrix-') as tmp:
            directory=Path(tmp);state=directory/'state';state.mkdir()
            bindings={}
            if comparison: bindings['comparison']={'provider':'openai','model':'gpt-4.1-mini'}
            if voice:
                bindings.update(speech_to_text={'provider':'openai','model':'gpt-transcribe'},
                                text_to_speech={'provider':'openai','model':'gpt-realtime-2.1-mini'})
            instance=str(uuid4());proof='a'*64
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG')}
            env.update(LANGFUSE_BASE_URL=base,LANGFUSE_PUBLIC_KEY='pk-matrix',LANGFUSE_SECRET_KEY='sk-matrix',
                       LLM_PROVIDER='anthropic',LLM_MODEL='claude-sonnet-4-6',LLM_API_KEY='fixture-chat',
                       ANTHROPIC_API_KEY='fixture-chat',SYNTH_STATE_DIR=str(state),SYNTH_OUT_DIR=str(directory/'out'),
                       PORTAL_LIVE_INSTANCE_ID=instance,PORTAL_LIVE_PROOF=proof,
                       LIVE_BASE_PATH='/live/'+instance,PORTAL_LIVE_PUBLIC_ORIGIN='http://localhost:3009',
                       PORTAL_LIVE_PROTECTED_PATHS=json.dumps([
                           {'path':'/manage','access':'manage'},{'path':'/apm','access':'presenter'},
                           {'path':'/n8n','access':'presenter'}]),
                       DEPOT_OPERATION_TOKEN='b'*64,
                       DEPOT_OPERATION_CHECK_URL=f'http://caddy:8081/api/v1/live-assets/{instance}/operation-permit')
            if bindings:
                env.update(OPENAI_API_KEY='fixture-secondary',LLM_ROLES_JSON=json.dumps({'version':1,'bindings':bindings}))
            generation=['synth','generate-spool','--config','config/demo.yaml','--set','generation.target_traces=58','--set','generation.as_of_date=2026-10-23']
            for name,value in flags.items():generation.extend(['--set','runtime.'+name+'='+str(value).lower()])
            seeded=subprocess.run(generation,env=env,capture_output=True,text=True,timeout=30)
            assert seeded.returncode==0,seeded.stdout+seeded.stderr
            state.chmod(0o555)
            with socket.socket() as available:
                available.bind(('127.0.0.1',0));port=available.getsockname()[1]
            started=time.monotonic()
            with (directory/'companion.log').open('w+') as log:
                child=subprocess.Popen(['synth','companion','--config','config/demo.yaml','--host','127.0.0.1','--port',str(port)],
                    env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                try:
                    deadline=started+30;health=None
                    while time.monotonic()<deadline and child.poll() is None:
                        try:
                            health=get(f'http://127.0.0.1:{port}/api/health')
                            if health.get('ready'):break
                        except (OSError,ValueError):pass
                        time.sleep(.1)
                    if not health or not health.get('ready'):
                        log.seek(0);raise AssertionError({'flags':flags,'health':health,'log':log.read()[-6000:]})
                    assert not writes, 'Readiness performed target writes'
                    assert set(health['detail'].get('roles',{}))==set(bindings), 'Unexpected enabled role'
                    info=get(f'http://127.0.0.1:{port}/api/info',proof)
                    assert info['capabilities']['voice']==voice
                    labels={item['label'] for item in info['links']}
                    assert ('APM' in labels)==apm
                    assert ('Complaint workflow' in labels)==workflow
                    results.append({'options':flags,'ready_seconds':round(time.monotonic()-started,3),
                                    'target_writes':len(writes),'empty_otlp_probes':len(pings),'secondary_key_bound':bool(bindings)})
                finally:
                    child.send_signal(signal.SIGTERM) if child.poll() is None else None
                    try:child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
                        raise AssertionError('Companion exceeded its shutdown bound')
                    state.chmod(0o755)
                # Uvicorn restores/re-raises SIGTERM after lifespan shutdown.
                assert child.returncode in (0,-signal.SIGTERM), ('Unclean Companion exit',child.returncode)
            deadline=time.monotonic()+3
            while processes()-before and time.monotonic()<deadline:time.sleep(.05)
            assert not processes()-before, ('Auxiliary processes survived',processes()-before)
            assert not writes, 'Lifecycle performed target writes'
            results[-1]['no_surviving_children']=True
            print(json.dumps(results[-1]),flush=True)
    print(json.dumps({'uid':os.getuid(),'combinations':results,'limits':{
        key:cgroup(key) for key in ('memory.max','memory.peak','pids.max','pids.peak','cpu.max')},
        'scope':'Model-free command/readiness/lifecycle under actual image constraints; real target egress and paid flows require separate qualification.'}))
finally:
    server.shutdown();server.server_close();thread.join(timeout=2)
