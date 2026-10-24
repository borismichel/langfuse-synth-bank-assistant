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
def target_server(version='4.59.0', reject_import=False, evidence=None, barrier=None):
    """The external API seam: no kit implementation collaborators are replaced."""
    posts = []
    probe_rows = []
    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if barrier and barrier[0] in self.path and not barrier[1].is_set():
                barrier[1].set()
                assert barrier[2].wait(10), 'public API barrier was not released'
            if self.path.startswith('/api/public/health'):
                body = {'version': version}
            elif self.path.startswith('/api/public/projects'):
                body = {'data': [{'id': 'test-demo-project', 'name': 'bank-demo'}]}
            elif evidence is not None and self.path.startswith(('/api/public/v2/observations', '/api/public/v3/scores')):
                from urllib.parse import urlparse, parse_qs
                url = urlparse(self.path)
                params = parse_qs(url.query)
                rows = evidence['scores' if url.path.endswith('/scores') else 'observations'] + (probe_rows if url.path.endswith('/observations') else [])
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
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.path.endswith('/otel/v1/traces') and not reject_import:
                import gzip
                from datetime import datetime, timezone
                if self.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.decompress(raw)
                batch = json.loads(raw)
                for resource in batch['resourceSpans']:
                    for scope in resource['scopeSpans']:
                        for span in scope['spans']:
                            if span['name'] in ('northwind.probe.backdate_check', 'probe.marker'):
                                probe_rows.append(dict(id=span['spanId'], traceId=span['traceId'],
                                    parentObservationId=span.get('parentSpanId'), type='SPAN', name=span['name'],
                                    startTime=datetime.fromtimestamp(int(span['startTimeUnixNano'])/1e9, timezone.utc).isoformat(),
                                    environment='staging', metadata={}, input=None, output=None))
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


def test_configured_history_window_and_exact_volume_plan(tmp_path):
    from datetime import datetime, timezone
    from langfuse_synth_core.seed.count import count_spool
    args = ('--set', 'generation.target_traces=58', '--set', 'generation.history_days=2',
            '--set', 'generation.as_of_date=2026-10-20')
    generated = invoke(tmp_path, 'generate-spool', *args)
    assert generated.returncode == 0, generated.stderr
    rows = [json.loads(line) for line in (tmp_path / 'state/events.ndjson').read_text().splitlines()]
    dates = {datetime.fromtimestamp(int(row['startTimeUnixNano']) / 1e9, timezone.utc).date().isoformat()
             for row in rows if row.get('name') == 'northwind-assistant'}
    assert dates == {'2026-10-19', '2026-10-20'}, dates
    before = (tmp_path / 'state/pending-run.json').read_bytes()
    planned = invoke(tmp_path, 'plan', *args)
    assert planned.returncode == 0, planned.stderr
    counts = json.loads(next(line.split('=', 1)[1] for line in planned.stdout.splitlines() if line.startswith('PLAN_COUNTS=')))
    measured = count_spool(tmp_path / 'state/events.ndjson')
    assert counts == {'version':1, **{k: measured[k] for k in ('traces', 'observations', 'scores')}}
    assert counts == {'version': 1, 'traces': 58, 'observations': 396, 'scores': 480}
    assert (tmp_path / 'state/pending-run.json').read_bytes() == before
    assert 'Seed model calls: 0' in planned.stdout


def test_configuration_rejects_invalid_types_ranges_and_unknown_generation_fields(tmp_path):
    for value in ('generation.target_traces=true', 'generation.target_traces=58.5',
                  'generation.history_days=0', 'generation.history_days=91',
                  'generation.seed=-1', 'generation.seed=0.5', 'generation.unknown=1'):
        result = invoke(tmp_path, 'generate-spool', '--set', value)
        assert result.returncode != 0, value
        assert not (tmp_path / 'state/events.ndjson').exists()


def test_probe_verifies_backdated_ingestion_before_bulk_setup(tmp_path):
    with target_server(evidence={'observations': [], 'scores': []}) as (env, posts):
        result = invoke(tmp_path, 'probe', extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(path.endswith('/otel/v1/traces') for path in posts)
    assert 'PROBE PASSED' in result.stdout


def test_setup_reports_progress_through_a_pipe_while_target_work_is_blocked(tmp_path):
    from threading import Event
    entered, release = Event(), Event()
    with target_server(barrier=('/api/public/v2/prompts/', entered, release)) as (env, _):
        runtime = {k: v for k,v in os.environ.items() if not any(key in k for key in ('LANGFUSE','ANTHROPIC','OPENAI','PROXY','proxy'))}
        runtime.update(env, PYTHONPATH=str(ROOT/'src'), SYNTH_STATE_DIR=str(tmp_path/'state'))
        process = subprocess.Popen([sys.executable, '-m', 'synth.cli', 'setup-prompts', '--config', 'config/demo.yaml'],
                                   cwd=ROOT, env=runtime, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert entered.wait(8), 'setup never reached the public prompt API'
            assert process.poll() is None
            # CLI phase output must already be available while the API call is blocked.
            import select
            assert select.select([process.stdout], [], [], 1)[0], 'buffered phase output'
            first = process.stdout.readline()
            assert 'Starting setup-prompts' in first
        finally:
            release.set()
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, err
        assert 'Resolved historical prompt 3/3' in out


def test_reduced_default_and_maximum_candidate_plans_count_actual_records(tmp_path):
    from langfuse_synth_core.seed.count import count_spool
    for volume, observations, scores in [(256, 1980, 2262), (64, 444, 534), (58, 396, 480)]:
        args = ('--set', f'generation.target_traces={volume}', '--set', 'generation.history_days=90',
                '--set', 'generation.as_of_date=2027-01-01')
        plan = invoke(tmp_path, 'plan', *args)
        assert plan.returncode == 0, plan.stderr
        counted = json.loads(next(line.split('=',1)[1] for line in plan.stdout.splitlines() if line.startswith('PLAN_COUNTS=')))
        assert counted == {'version':1, 'traces':volume, 'observations':observations, 'scores':scores}
        assert 'qualification is pending' in plan.stdout
        assert invoke(tmp_path, 'generate-spool', *args).returncode == 0
        measured = count_spool(tmp_path/'state/events.ndjson')
        assert all(counted[key] == measured[key] for key in ('traces','observations','scores'))
        pending = json.loads((tmp_path/'state/pending-run.json').read_text())
        assert len(pending['receipt']['history_anchors']['cases']) == 58


def test_verification_names_current_case_through_pipe_before_blocked_read(tmp_path):
    from threading import Event
    from test_history import api_evidence
    from asset_target import asset_target
    entered, release = Event(), Event()
    entered.set()  # Prepare the target before enabling the read barrier.
    with asset_target(barrier=('traceId=', entered, release)) as (env, evidence):
        assert invoke(tmp_path, 'setup-assets', extra_env=env).returncode == 0
        assert invoke(tmp_path, 'generate-spool', extra_env=env).returncode == 0
        assert invoke(tmp_path, 'import-spool', extra_env=env).returncode == 0
        evidence.update(api_evidence(tmp_path/'state/events.bound.ndjson'))
        assert invoke(tmp_path, 'prepare-cohorts', extra_env=env).returncode == 0
        entered.clear()
        runtime = {k:v for k,v in os.environ.items() if not any(key in k for key in ('LANGFUSE','ANTHROPIC','OPENAI','PROXY','proxy'))}
        runtime.update(env, PYTHONPATH=str(ROOT/'src'), SYNTH_STATE_DIR=str(tmp_path/'state'), SYNTH_OUT_DIR=str(tmp_path/'out'))
        process = subprocess.Popen([sys.executable, '-m', 'synth.cli', 'verify', '--timeout-seconds', '0', '--config', 'config/demo.yaml'],
                                   cwd=ROOT, env=runtime, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert entered.wait(8), 'verification never reached current-run read'
            assert process.poll() is None
            import select
            assert select.select([process.stdout], [], [], 1)[0], 'buffered verification phase'
            before = os.read(process.stdout.fileno(), 65536).decode()
            assert 'Checking historical turn 1/58:' in before
        finally:
            release.set()
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, out.decode() + err.decode()
        assert 'Checked historical turn 58/58: visible' in out.decode()
