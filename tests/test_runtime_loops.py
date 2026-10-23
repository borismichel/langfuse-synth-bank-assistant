"""Isolate real SDK clients and provider keep-alive connections from offline tests."""
import os
import subprocess
import sys


def test_live_and_experiment_event_loop_ownership():
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env.update(PYTHONPATH='src:tests:demos/bank-assistant',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result=subprocess.run([sys.executable,'-m','pytest','tests/sdk_runtime_loop_case.py','-q'],
        env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert '1 passed' in result.stdout
