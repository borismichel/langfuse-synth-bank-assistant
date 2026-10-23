"""Owned subprocess keeps SDK/MCP HTTP integration outside the offline socket guard."""
import os
import signal
import subprocess
import sys


def test_delivered_prompt_release_controls():
    env = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src','tests','demos/bank-assistant'])
    process = subprocess.Popen([sys.executable,'-m','pytest','tests/sdk_promotions_case.py','-q'],
        env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        output, errors = process.communicate(timeout=90)
    finally:
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)
    assert process.returncode==0, output+errors
