"""Governance through delivered HTTP, using actual SDK and external API fixtures."""
from fastapi.testclient import TestClient


def test_project_api_governance_readback_and_private_access():
    import os
    import subprocess
    import sys
    env = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src','tests','demos/bank-assistant'])
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/sdk_governance_case.py', '-q'],
                            env=env, text=True, capture_output=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr


def test_preview_labels_governance_as_documentation_without_target_access(monkeypatch):
    from synth.companion.app import create_app
    monkeypatch.setenv('LIVE_BASE_PATH', '/live/governance-example')
    browser = TestClient(create_app(None, preview=True))
    page = browser.get('/manage/governance')
    assert page.status_code == 200
    assert 'Documentation chapter' in page.text
    assert '/live/governance-example/static/app.css' in page.text
    assert 'ordinary project API key' in page.text
    status = browser.get('/manage/governance/status').json()
    assert status['connection']['status'] == 'disabled'
    assert status['target'] is None
    assert all(item['status'] == 'unavailable' for item in status['capabilities'])
    assert all(item['reason'] and item['documentation_url'] and item['native_url'] is None
               for item in status['capabilities'])
    assert '/live/governance-example/manage/governance' in browser.get('/').text
