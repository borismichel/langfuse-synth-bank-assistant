"""Native SDK read-only governance discovery through a local external target."""
import json
from fastapi.testclient import TestClient
from experiment_target import experiment_target


def test_retention_readback_does_not_imply_enterprise_or_user_permissions(monkeypatch):
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from northwind import config
    from synth.config import load_config
    from synth.companion.app import create_app, REQUIRES_SECRETS
    with experiment_target() as (env, target):
        for key, value in env.items(): monkeypatch.setenv(key, value)
        monkeypatch.setenv('LIVE_BASE_PATH', '/live/governance-fixture')
        monkeypatch.setenv('PORTAL_LIVE_PROOF', 'a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS', json.dumps([{'path':'/manage/governance','access':'manage'}]))
        project = dict(id='bank-project', name='<script>bad()</script>', organization={'id':'org','name':'Enterprise'},
                       metadata={'entitlements': ['audit-logs'], 'role':'OWNER'}, retentionDays=30)
        reads = []
        version = ['4.68.0']
        projects = [project]
        def extension(method, path, query, body):
            reads.append((method, path))
            if path == '/api/public/projects': return 200, {'data':projects}
            if path == '/api/public/health': return 200, {'status':'OK','version':version[0]}
        target['extension'] = extension
        adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
            langfuse_factory=config.langfuse_factory, native_llm_factory=config.native_llm_factory)
        app = create_app(adapter)
        browser = TestClient(guarded_app(app, health_path='/api/health'))
        assert browser.get('/manage/governance/status').status_code == 404
        assert browser.get('/manage/governance/status?x-depot-live-proof='+'a'*64).status_code == 404
        assert not reads
        browser.headers['x-depot-live-proof'] = 'a'*64
        status = browser.get('/manage/governance/status').json()
        assert status['connection']['status'] == 'available', status
        assert status['target']['id'] == 'bank-project'
        assert status['target']['version'] == '4.68.0'
        assert status['checked_at']
        items = {c['id']:c for c in status['capabilities']}
        assert items['retention']['status'] == 'available'
        assert items['retention']['days'] == 30
        assert items['retention']['evidence'] == '/api/public/projects: retentionDays'
        for key in ('rbac','audit','protected_labels'):
            assert items[key]['status'] == 'unavailable'
            assert items[key]['reason'] and items[key]['native_url'].startswith(env['LANGFUSE_BASE_URL']+'/project/bank-project')
        page = browser.get('/manage/governance')
        assert page.status_code == 200 and 'Inspect in Langfuse' in page.text
        assert '<script>bad()' not in page.text
        assert '&lt;script&gt;bad()' in page.text
        assert 'test-secret' not in json.dumps(status)
        for value in (0, None, 'omitted'):
            project['retentionDays'] = value
            if value == 'omitted': del project['retentionDays']
            result = browser.get('/manage/governance/status').json()
            retention = next(c for c in result['capabilities'] if c['id']=='retention')
            assert retention['status'] == 'disabled', result
            assert retention['days'] == 0
            assert 'automatic event deletion' in retention['reason']
        version[0] = '4.69.0'
        result = browser.get('/manage/governance/status').json()
        assert next(c for c in result['capabilities'] if c['id']=='retention')['status'] == 'unavailable'
        assert result['connection']['status'] == 'available'
        version[0] = '4.68.0'
        project['retentionDays'] = 2
        result = browser.get('/manage/governance/status').json()
        assert next(c for c in result['capabilities'] if c['id']=='retention')['status'] == 'failed'
        target['deny_read'] = '/api/public/projects'
        before = len(reads)
        result = browser.get('/manage/governance/status').json()
        assert result['connection']['status'] == 'failed'
        assert '403' in result['connection']['reason']
        assert result['target'] is None
        assert all(c['native_url'] is None for c in result['capabilities'])
        assert len(reads) - before <= 1  # No SDK automatic retry of permission denial.
        target.pop('deny_read')
        projects.append({**project, 'id':'another-project'})
        result = browser.get('/manage/governance/status').json()
        assert result['connection']['status'] == 'failed' and result['target'] is None
        assert browser.post('/manage/governance', json={'retentionDays':3}).status_code == 405
        assert not target['writes'] and all(method == 'GET' for method, _ in reads)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS', '[]')
        assert TestClient(app).get('/manage/governance/status').status_code == 503
        adapter.langfuse().shutdown()
