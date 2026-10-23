"""Voice configuration and delivered HTTP behavior; no live credentials."""
import json

from test_pipeline import invoke


def test_voice_option_is_model_free_and_preserves_history(tmp_path):
    args = ('--set', 'generation.as_of_date=2026-10-20')
    off = invoke(tmp_path, 'generate-spool', *args)
    assert off.returncode == 0, off.stderr
    spool = (tmp_path / 'state/events.ndjson').read_bytes()
    on = invoke(tmp_path, 'generate-spool', *args, '--set', 'runtime.voice_enabled=true')
    assert on.returncode == 0, on.stderr
    runtime = json.loads((tmp_path / 'state/runtime-config.json').read_text())
    assert runtime['voice_enabled'] is True
    assert (tmp_path / 'state/events.ndjson').read_bytes() == spool


def test_voice_option_rejects_non_boolean_before_generation(tmp_path):
    result = invoke(tmp_path, 'generate-spool', '--set', 'runtime.voice_enabled=enabled')
    assert result.returncode != 0
    assert not (tmp_path / 'state/events.ndjson').exists()


def run_voice_fixture(extra_env=None):
    import os
    import signal
    import subprocess
    import sys
    env = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG')}
    env['PYTHONPATH']='src:demos/bank-assistant'
    env.update(extra_env or {})
    child = subprocess.Popen([sys.executable,'tests/sdk_voice_case.py'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        out, err = child.communicate(timeout=60)
        assert child.returncode == 0, out + err
        markers = {
            'VOICE_MEDIA_REFERENCE_MISMATCH':'foreign_media_reference_rejected=true',
            'VOICE_MEDIA_FAILURE':'failed_media_preserves_answer=true',
            'VOICE_CAPACITY':'pending_media_capacity=true',
            'VOICE_SHUTDOWN':'bounded_shutdown=true',
        }
        expected = next((marker for key,marker in markers.items() if (extra_env or {}).get(key)), '"duplicate_replay": true')
        assert expected in out
    finally:
        try: os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(timeout=5)


def test_manifest_enables_only_explicit_independent_speech_roles():
    import yaml
    from pathlib import Path
    manifest=yaml.safe_load(Path('usecase.yaml').read_text())
    assert manifest['config_schema']['properties']['runtime.voice_enabled']['default'] is False
    bindings=manifest['llm']['roles']['bindings']
    assert {name:(bindings[name]['provider'],bindings[name]['models']['openai'],bindings[name]['enabled_by']) for name in ('speech_to_text','text_to_speech')} == {
        'speech_to_text':('openai','gpt-transcribe','runtime.voice_enabled'),
        'text_to_speech':('openai','gpt-realtime-2.1-mini','runtime.voice_enabled')}
    component=next(row for row in manifest['live_components'] if row['name']=='companion')
    assert {'speech_to_text','text_to_speech'} <= set(component['llm_roles'])
    assert component['spending_check']=={'version':1}


def test_voice_uses_owned_conversation_and_independent_speech_roles():
    run_voice_fixture()

def test_voice_shutdown_does_not_wait_for_remote_media():
    run_voice_fixture({"VOICE_SHUTDOWN":"1"})


def test_pending_media_keeps_capacity_reserved_across_new_conversations():
    run_voice_fixture({'VOICE_CAPACITY':'1'})


def test_voice_storage_upload_failure_is_explicit_without_replaying_bank_work():
    run_voice_fixture({'VOICE_MEDIA_FAILURE':'1'})


def test_voice_rejects_media_reference_for_different_owned_bytes():
    run_voice_fixture({'VOICE_MEDIA_REFERENCE_MISMATCH':'1'})
