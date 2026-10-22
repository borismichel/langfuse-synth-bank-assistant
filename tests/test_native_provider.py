"""Native HTTP clients retain exactly one generation per LangGraph model call."""
import pytest

@pytest.mark.parametrize('provider', ['anthropic', 'openai'])
def test_native_provider_counts_each_call_once(provider):
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env.update(BANK_TEST_PROVIDER=provider, PYTHONPATH=os.pathsep.join(['src', 'demos/bank-assistant']))
    result = subprocess.run([sys.executable, 'tests/sdk_native_provider_case.py'], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"generations": 6' in result.stdout

