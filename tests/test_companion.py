"""Delivered Companion HTTP boundary, with credential-free preview."""
import pytest
from fastapi.testclient import TestClient



def test_owned_sessions_reset_and_answer_feedback():
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src', 'demos/bank-assistant'])
    result = subprocess.run([sys.executable, 'tests/sdk_sessions_case.py'], env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"feedback": true' in result.stdout


def test_delivered_banking_uses_private_mcp_and_real_sdk():
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src', 'demos/bank-assistant'])
    result = subprocess.run([sys.executable, 'tests/sdk_mcp_case.py'], env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"mcp_read": true' in result.stdout


def test_preview_preserves_bilingual_interface_without_live_connections(monkeypatch):
    monkeypatch.setenv('LIVE_BASE_PATH', '/live/bank-test')
    from synth.companion.app import create_app
    with TestClient(create_app(None, preview=True)) as browser:
        page = browser.get('/')
        assert page.status_code == 200
        assert 'Development preview' in page.text
        assert '/live/bank-test/static/app.js' in page.text
        session = browser.post('/api/session').json()['session_id']
        assert browser.cookies.get('northwind_session') != session
        answer = browser.post('/api/chat', json={'message': '¿Cuánto cuesta una transferencia internacional?', 'session_id': session}, headers={'Cookie': 'northwind_session=' + browser.cookies.get('northwind_session')}).json()
        assert answer['preview'] is True
        assert 'USD 35' in answer['answer']
        assert not answer['trace_url']
        health = browser.get('/api/health')
        assert health.status_code == 503
        assert health.json()['ready'] is False


@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
def test_delivered_chat_preserves_real_sdk_trace_contract(provider):
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['BANK_TEST_PROVIDER'] = provider
    env['PYTHONPATH'] = os.pathsep.join(['src', 'demos/bank-assistant'])
    result = subprocess.run([sys.executable, 'tests/sdk_companion_case.py'], env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"masked": true' in result.stdout


def test_missing_runtime_credentials_leave_liveness_with_false_readiness(monkeypatch):
    import os
    from langfuse_synth_core.companion import CompanionAdapter
    from synth.companion.app import create_app, REQUIRES_SECRETS
    from synth.config import load_config
    from northwind import config
    for key in list(os.environ):
        if any(marker in key for marker in ('LANGFUSE', 'ANTHROPIC', 'OPENAI', 'LLM_')):
            monkeypatch.delenv(key)
    adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
        langfuse_factory=config.langfuse_factory, native_llm_factory=config.native_llm_factory)
    with TestClient(create_app(adapter)) as browser:
        response = browser.get('/api/health')
        assert response.status_code == 200
        assert response.json()['ready'] is False
        assert response.json()['llm_bound'] is False
        assert browser.get('/').status_code == 200


def test_customer_cannot_select_someone_elses_conversation_or_execute_a_job():
    from synth.companion.app import create_app
    with TestClient(create_app(None, preview=True)) as browser:
        first = browser.post('/api/session').json()['session_id']
        browser.post('/api/session')
        response = browser.post('/api/chat', json={'session_id': first, 'message': 'International wire fees?'})
        assert response.status_code == 409
        assert browser.post('/api/run/anything').status_code == 404
        assert browser.post('/api/voice/process').status_code == 503
        assert browser.post('/api/feedback', json={'session_id': first, 'trace_id': 'someone-else',
                                                 'observation_id': 'foreign', 'value': 1}).status_code == 403
