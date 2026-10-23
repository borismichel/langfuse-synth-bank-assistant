"""Optional workflow lifecycle and delivered configuration contract."""
import asyncio


def test_disabled_workflow_starts_nothing():
    from synth.companion.n8n import N8nService
    service = N8nService(enabled=False, base_path='/live/example/n8n', bridge_url='', token='')
    asyncio.run(service.start())
    assert not service.ready and service.process is None and service.directory is None
    asyncio.run(service.stop())


def test_n8n_option_uses_separate_runtime_state_without_changing_history(tmp_path):
    import json
    import os
    import subprocess
    import sys
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'TMPDIR', 'LANG'}}
    env.update(PYTHONPATH='src:demos/bank-assistant', SYNTH_STATE_DIR=str(tmp_path/'state'), SYNTH_OUT_DIR=str(tmp_path/'out'))
    command = [sys.executable, '-m', 'synth.cli', 'generate-spool', '--config', 'config/demo.yaml']
    off = subprocess.run(command, env=env, capture_output=True, text=True)
    assert off.returncode == 0, off.stderr
    spool = (tmp_path/'state/events.ndjson').read_bytes()
    on = subprocess.run(command + ['--set', 'runtime.n8n_enabled=true'], env=env, capture_output=True, text=True)
    assert on.returncode == 0, on.stderr
    assert json.loads((tmp_path/'state/runtime-config.json').read_text())['n8n_enabled'] is True
    assert (tmp_path/'state/events.ndjson').read_bytes() == spool


def test_complaint_http_and_real_sdk_contract():
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = 'src:demos/bank-assistant'
    result = subprocess.run([sys.executable, 'tests/n8n_bridge_case.py'], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
