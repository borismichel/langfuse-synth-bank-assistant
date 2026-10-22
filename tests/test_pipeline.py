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


def test_plan_and_generate_deliver_three_banking_examples_without_credentials(tmp_path):
    plan = invoke(tmp_path, 'plan')
    assert plan.returncode == 0, plan.stderr
    counts = json.loads(next(line.removeprefix('PLAN_COUNTS=') for line in plan.stdout.splitlines() if line.startswith('PLAN_COUNTS=')))
    assert counts == {'version': 1, 'traces': 3, 'observations': 9, 'scores': 3}
    generated = invoke(tmp_path, 'generate-spool', '--set', 'generation.as_of_date=2026-10-20')
    assert generated.returncode == 0, generated.stderr
    events = [json.loads(line) for line in (tmp_path / 'state/events.ndjson').read_text().splitlines()]
    roots = [e for e in events if e.get('name', '').startswith('bank-dispute-')]
    assert [e['name'] for e in roots] == ['bank-dispute-baseline', 'bank-dispute-failure', 'bank-dispute-recovery']
    assert [e['body']['value'] for e in events if e.get('type') == 'score-create'] == [1, 0, 1]
    assert not (tmp_path / 'state/.synth_verify.json').exists()
    assert 'Synthetic' in (tmp_path / 'out/DEMO_SCRIPT.md').read_text()


def test_generation_rejects_volume_outside_declared_range(tmp_path):
    result = invoke(tmp_path, 'generate-spool', '--set', 'generation.target_traces=25')
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
def target_server(version='4.59.0', reject_import=False):
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
        assert invoke(tmp_path, 'generate-spool', extra_env=env).returncode == 0
        result = invoke(tmp_path, 'import-spool', extra_env=env)
        assert result.returncode == 1
        assert len(posts) == 1
        assert invoke(tmp_path, 'import-spool', extra_env=env).returncode == 1
        assert len(posts) == 1
    assert not (tmp_path / 'state/.synth_verify.json').exists()


def test_successful_import_delivers_links_but_empty_target_does_not_verify(tmp_path):
    with target_server() as (env, posts):
        assert invoke(tmp_path, 'generate-spool', '--set', 'generation.seed=17', extra_env=env).returncode == 0
        # Depot's synthesized import deliberately receives no --set overrides.
        imported = invoke(tmp_path, 'import-spool', extra_env=env)
        assert imported.returncode == 0, imported.stderr
        assert '/api/public/otel/v1/traces' in posts
        assert '/project/test-demo-project/traces/' in (tmp_path / 'out/DEMO_SCRIPT.md').read_text()
        result = invoke(tmp_path, 'verify', '--timeout-seconds', '0', '--set', 'generation.seed=17', extra_env=env)
        assert result.returncode == 1
        assert 'missing trace' in result.stdout
