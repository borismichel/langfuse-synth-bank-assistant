"""Run the delivered comparison scenario outside the model-free parent guard."""
import os,signal,subprocess,sys
import pytest


@pytest.mark.parametrize('scenario',['controls_are_protected','matched_frozen','legacy_is','cap_denial','cancellation_and','edited_fixed'])
def test_delivered_conduct_comparison(scenario):
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env['PYTHONPATH']=os.pathsep.join(['src','tests','demos/bank-assistant'])
    process=subprocess.Popen([sys.executable,'-m','pytest','tests/sdk_conduct_case.py','-q','-k',scenario],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:output,errors=process.communicate(timeout=120)
    finally:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=5)
    assert process.returncode==0,output+errors
