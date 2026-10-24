"""Optional provider comparison through delivered routes and native HTTP clients."""
import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest


@pytest.mark.parametrize('scenario', [
    'test_provider_delivery_and_model_identity',
    'test_provider_binding_and_incomplete_native_accounting',
    'test_provider_failures_and_missing_usage',
    'test_provider_dispute_turn_accounting',
    'test_disabled_comparison_resolves_no_extra_provider_or_paid_work',
])
def test_provider_comparison_uses_declared_role_and_measured_native_evidence(scenario):
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env['PYTHONPATH']=os.pathsep.join(['src','tests','demos/bank-assistant'])
    # Isolate SDK globals and local receipts; retain the original per-process cap.
    with tempfile.TemporaryDirectory(prefix='provider-comparison-') as folder:
        env['TMPDIR']=folder
        process=subprocess.Popen([sys.executable,'-m','pytest',
            'tests/sdk_provider_comparison_case.py::'+scenario,'-vv','-s',
            '-o','faulthandler_timeout=75','--durations=5'],env=env,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        started=time.monotonic()
        timed_out=False
        try:
            try:out,err=process.communicate(timeout=90)
            except subprocess.TimeoutExpired:timed_out=True
        finally:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            out,err=process.communicate(timeout=5)
        elapsed=time.monotonic()-started
        assert not timed_out, f'{scenario} exceeded 90s ({elapsed:.3f}s)\n{out}\n{err}'
        assert process.returncode==0,out+err
