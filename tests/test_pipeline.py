"""Approved seam: execute delivered Manifest commands with isolated output/state."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def invoke(tmp_path, *args, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not any(s in k for s in ('LANGFUSE', 'ANTHROPIC', 'OPENAI', 'PROXY', 'proxy'))}
    env.update(PYTHONPATH=str(ROOT / 'src'), SYNTH_STATE_DIR=str(tmp_path / 'state'), SYNTH_OUT_DIR=str(tmp_path / 'out'))
    env.update(extra_env or {})
    return subprocess.run([sys.executable, '-m', 'synth.cli', *args, '--config', str(ROOT / 'config/demo.yaml')], cwd=ROOT, env=env, capture_output=True, text=True)


def test_plan_and_generate_deliver_complete_banking_history_without_credentials(tmp_path):
    from langfuse_synth_core.seed.count import count_spool
    plan = invoke(tmp_path, 'plan')
    assert plan.returncode == 0, plan.stderr
    counts = json.loads(next(line.removeprefix('PLAN_COUNTS=') for line in plan.stdout.splitlines() if line.startswith('PLAN_COUNTS=')))
    generated = invoke(tmp_path, 'generate-spool', '--set', 'generation.as_of_date=2026-10-20')
    assert generated.returncode == 0, generated.stderr
    measured = count_spool(tmp_path / 'state/events.ndjson')
    assert counts == {'version': 1, **{k: measured[k] for k in ('traces', 'observations', 'scores')}}
    assert counts['traces'] == 64 and counts['observations'] > 400 and counts['scores'] > 400
    assert not (tmp_path / 'state/.synth_verify.json').exists()
    pending = json.loads((tmp_path / 'state/pending-run.json').read_text())
    assert len(pending['receipt']['history_anchors']['cases']) == 58
    assert 'Synthetic' in (tmp_path / 'out/DEMO_SCRIPT.md').read_text()


def test_generation_rejects_volume_outside_declared_range(tmp_path):
    result = invoke(tmp_path, 'generate-spool', '--set', 'generation.target_traces=57')
    assert result.returncode != 0
    assert not (tmp_path / 'state/events.ndjson').exists()


def test_plan_does_not_mutate_existing_deployment_state(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'events.ndjson').write_text('existing spool')
    (state / '.synth_verify.json').write_text('existing receipt')
    assert invoke(tmp_path, 'plan').returncode == 0
    assert (state / 'events.ndjson').read_text() == 'existing spool'
    assert (state / '.synth_verify.json').read_text() == 'existing receipt'


def test_regeneration_cannot_erase_import_replay_protection(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'events.ndjson.imported').write_text('import started')
    result = invoke(tmp_path, 'generate-spool')
    assert result.returncode != 0
    assert 'fresh target' in result.stderr
    assert (state / 'events.ndjson.imported').read_text() == 'import started'


def test_verify_cannot_accept_unrelated_old_data_without_current_receipt(tmp_path):
    result = invoke(tmp_path, 'verify', '--timeout-seconds', '0')
    assert result.returncode == 1
    assert 'Missing or invalid receipt' in result.stdout


from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


@contextmanager
def target_server(version='4.59.0', reject_import=False, evidence=None):
    """The external API seam: no kit implementation collaborators are replaced."""
    posts = []
    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith('/api/public/health'):
                body = {'version': version}
            elif self.path.startswith('/api/public/projects'):
                body = {'data': [{'id': 'test-demo-project', 'name': 'bank-demo'}]}
            elif evidence is not None and self.path.startswith(('/api/public/v2/observations', '/api/public/v3/scores')):
                from urllib.parse import urlparse, parse_qs
                url = urlparse(self.path)
                params = parse_qs(url.query)
                rows = evidence['scores' if url.path.endswith('/scores') else 'observations']
                trace_id = params.get('traceId', [None])[0]
                body = {'data': [row for row in rows if trace_id is None or
                    (row.get('traceId') or row.get('subject', {}).get('traceId')) == trace_id], 'meta': {}}
            elif self.path.startswith('/api/public/v2/prompts/'):
                from urllib.parse import unquote, urlparse
                from synth.materialize import fixture
                from northwind.prompts import PROMPT_NAME, V5_RELEASE
                name = unquote(urlparse(self.path).path.rsplit('/', 1)[-1])
                text = V5_RELEASE if name == PROMPT_NAME else next(
                    text for key, text in fixture()['prompts'].items()
                    if name.startswith('northwind-history-' + key + '-'))
                body = dict(name=name, prompt=text, version=7, type='text',
                            labels=['latest', 'production'], tags=['northwind-bank', 'depot-managed'], config={})
            else:
                body = {'data': [], 'meta': {}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            posts.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(400 if reject_import else 200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"successes":[],"errors":[]}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), TargetHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {'LANGFUSE_BASE_URL': f'http://127.0.0.1:{server.server_port}',
               'LANGFUSE_PUBLIC_KEY': 'test-public', 'LANGFUSE_SECRET_KEY': 'test-secret'}, posts
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_probe_rejects_unsupported_self_hosted_before_any_write(tmp_path):
    with target_server(version='4.58.0') as (env, posts):
        result = invoke(tmp_path, 'probe', extra_env=env)
    assert result.returncode == 1
    assert '4.59.0' in result.stderr
    assert posts == []


def test_partial_import_refuses_retry_and_does_not_publish_success_receipt(tmp_path):
    with target_server(reject_import=True) as (env, posts):
        assert invoke(tmp_path, 'setup-prompts', extra_env=env).returncode == 0
        assert invoke(tmp_path, 'generate-spool', extra_env=env).returncode == 0
        result = invoke(tmp_path, 'import-spool', extra_env=env)
        assert result.returncode == 1
        assert len(posts) == 1
        assert invoke(tmp_path, 'import-spool', extra_env=env).returncode == 1
        assert len(posts) == 1
    assert not (tmp_path / 'state/.synth_verify.json').exists()


def test_successful_import_delivers_links_but_empty_target_does_not_verify(tmp_path):
    with target_server() as (env, posts):
        assert invoke(tmp_path, 'setup-prompts', extra_env=env).returncode == 0
        assert invoke(tmp_path, 'generate-spool', '--set', 'generation.seed=17', extra_env=env).returncode == 0
        # Depot's synthesized import deliberately receives no --set overrides.
        imported = invoke(tmp_path, 'import-spool', extra_env=env)
        assert imported.returncode == 0, imported.stderr
        assert '/api/public/otel/v1/traces' in posts
        assert '/project/test-demo-project/traces/' in (tmp_path / 'out/DEMO_SCRIPT.md').read_text()
        result = invoke(tmp_path, 'verify', '--timeout-seconds', '0', '--set', 'generation.seed=17', extra_env=env)
        assert result.returncode == 1
        assert 'missing trace' in result.stdout
