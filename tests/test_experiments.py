"""Presenter experiments through their delivered authenticated HTTP boundary."""
import os
import signal
import subprocess
import sys
import pytest


def test_delivered_experiment_controls():
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src', 'tests', 'demos/bank-assistant'])
    process = subprocess.Popen([sys.executable, '-m', 'pytest', 'tests/sdk_experiments_case.py', '-q'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        output, errors = process.communicate(timeout=90)
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=5)
    assert process.returncode == 0, output + errors


@pytest.mark.parametrize('provider',['anthropic','openai'])
def test_actual_agent_exact_prompt_policy_usage_and_per_call_permission(provider):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src', 'tests', 'demos/bank-assistant'])
    env['BANK_TEST_PROVIDER']=provider
    result = subprocess.run([sys.executable, 'tests/sdk_experiment_agent_case.py'], env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('provider',['anthropic','openai'])
def test_categorical_judge_output_contract_and_invalid_results(provider):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src', 'tests', 'demos/bank-assistant'])
    env['BANK_TEST_PROVIDER'] = provider
    result = subprocess.run([sys.executable, 'tests/sdk_judge_format_case.py'], env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_judge_stops_delivered_comparison_without_retry():
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src', 'tests', 'demos/bank-assistant'])
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/sdk_invalid_judge_case.py', '-q'], env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
