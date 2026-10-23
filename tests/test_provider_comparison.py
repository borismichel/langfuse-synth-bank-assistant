"""Optional provider comparison through delivered routes and native HTTP clients."""
def test_provider_comparison_uses_declared_role_and_measured_native_evidence():
    import os,signal,subprocess,sys
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env['PYTHONPATH']=os.pathsep.join(['src','tests','demos/bank-assistant'])
    process=subprocess.Popen([sys.executable,'-m','pytest','tests/sdk_provider_comparison_case.py','-q'],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:out,err=process.communicate(timeout=90)
    finally:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=5)
    assert process.returncode==0,out+err
