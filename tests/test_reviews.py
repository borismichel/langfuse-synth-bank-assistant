"""Delivered review and calibration boundary; fixture networking runs in a child."""
import os
import subprocess
import sys


def test_native_review_and_calibration_actions():
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env.update(PYTHONPATH=os.pathsep.join(['src','tests','demos/bank-assistant']),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result=subprocess.run([sys.executable,'-m','pytest','tests/sdk_reviews_case.py','-q'],
        env=env,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'passed' in result.stdout
