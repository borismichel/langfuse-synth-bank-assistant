"""Approved pure reducer and delivered HTTP reconciliation boundaries."""
import os
import subprocess
import sys


def test_policy_truth_table_and_owned_reconciliation_with_actual_sdk():
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env.update(PYTHONPATH=os.pathsep.join(['src','tests','demos/bank-assistant']),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    result=subprocess.run([sys.executable,'-m','pytest','tests/sdk_reconcile_case.py',
        'demos/bank-assistant/tests/test_compliance.py::Aggregation','-q'],
        env=env,capture_output=True,text=True,timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'passed' in result.stdout
