"""Delivered managed-judge boundary, isolated from the offline authoring guard."""
import os
import subprocess
import sys


def test_managed_judge_lifecycle_with_real_sdk_and_local_target():
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env.update(PYTHONPATH=os.pathsep.join(['src','tests','demos/bank-assistant']),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result=subprocess.run([sys.executable,'-m','pytest','tests/sdk_judges_case.py','-q'],
        env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert '12 passed' in result.stdout
