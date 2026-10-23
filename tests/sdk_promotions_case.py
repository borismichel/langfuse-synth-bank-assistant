"""Delivered promotion controls with actual SDK/native HTTP experiment evidence."""
import copy
import json
import time

from experiment_target import experiment_target, provider_fixture
from test_assets import seeded_run
from test_pipeline import invoke


def test_passed_exact_candidate_changes_fresh_conversation_without_restart(tmp_path, monkeypatch, request):
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app, REQUIRES_SECRETS
    from northwind import config, prompts

    with experiment_target() as (env, target):
        seeded_run(tmp_path, env, target)
        result = invoke(tmp_path, 'prepare-cohorts', extra_env=env)
        assert result.returncode == 0, result.stderr
        state = tmp_path/'state'
        original_files = {p.name:p.read_bytes() for p in state.iterdir() if p.is_file()}
        state.chmod(0o555)
        request.addfinalizer(lambda:state.chmod(0o700))
        candidate = copy.deepcopy(target['versions'][(prompts.PROMPT_NAME, 1)])
        candidate.update(version=2, labels=['staging'], prompt=candidate['prompt']+'\nUse clear sentences.')
        target['versions'][(prompts.PROMPT_NAME, 2)] = candidate
        target['prompts'][prompts.PROMPT_NAME] = candidate
        provider_fixture(env, target, monkeypatch)
        for key, value in env.items(): monkeypatch.setenv(key, value)
        monkeypatch.setenv('SYNTH_STATE_DIR', str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_PROOF', 'a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS', json.dumps([
            {'path':'/manage/experiments','access':'manage'},
            {'path':'/manage/releases','access':'manage'}]))
        adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL, langfuse_factory=config.langfuse_factory,
            native_llm_factory=config.native_llm_factory)
        with TestClient(guarded_app(create_app(adapter), health_path='/api/health')) as browser:
            assert browser.get('/manage/releases').status_code == 404
            browser.headers['x-depot-live-proof'] = 'a'*64
            page = browser.get('/manage/releases')
            assert page.status_code == 200, page.text
            assert 'Prompt releases' in page.text
            body = dict(request_id='release-cohort', cohort='english', baseline_version=1,
                        candidate_version=2, candidate_policy='current', judge_model='fixture-judge',
                        cost_acknowledged=True)
            started = browser.post('/manage/experiments/start', json=body)
            assert started.status_code == 202, started.text
            identity = started.json()['id']
            deadline = time.monotonic()+40
            while time.monotonic() < deadline:
                run = browser.get('/manage/experiments/status').json()['runs'][0]
                if run['status'] != 'running': break
                time.sleep(.05)
            measured = browser.post('/manage/experiments/'+identity+'/readback', json={})
            assert measured.status_code == 200, measured.text
            assert measured.json()['gate']['status'] == 'passed', measured.text
            # Warm the normal production prompt cache before the label transition.
            assert prompts.get_system_prompt(adapter.langfuse())[1].version == 1
            release = browser.post('/manage/releases/promote', json={
                'request_id':'release-one', 'comparison_id':identity})
            assert release.status_code == 200, release.text
            record = release.json()
            assert record['status'] == 'promoted'
            assert record['old']['version'] == 1 and record['new']['version'] == 2
            assert record['comparison_id'] == identity
            current = browser.get('/manage/releases/status').json()
            assert current['production']['version']==2 and not current['comparisons'][0]['eligible']
            assert current['records'][0]['rollback_eligible']
            session = browser.post('/api/session').json()['session_id']
            question = next(i['input']['question'] for i in target['items'].values()
                            if i['metadata']['cohort']=='english')
            answer = browser.post('/api/chat', json={'session_id':session, 'request_id':'after-release', 'message':question})
            assert answer.status_code == 200, answer.text
            assert answer.json()['prompt_version'] == 2
            actual = [o for o in target['observations'] if o['id']==answer.json()['observation_id']]
            assert len(actual)==1 and json.loads(actual[0]['metadata']['prompt_version'])=='2'
            def generation_versions(reply):
                rows = [span for span in target['exports'] if span.trace_id.hex()==reply['trace_id']]
                attributes = [{a.key:str(getattr(a.value,a.value.WhichOneof('value'))) for a in span.attributes} for span in rows]
                return [a['langfuse.observation.prompt.version'] for a in attributes
                        if a.get('langfuse.observation.type')=='generation']
            assert generation_versions(answer.json()) == ['2','2']
            mutations = len([p for p,b in target['writes'] if '/versions/' in p])
            repeated = browser.post('/manage/releases/promote', json={'request_id':'release-one','comparison_id':identity})
            assert repeated.json()['id']==record['id']
            assert len([p for p,b in target['writes'] if '/versions/' in p])==mutations
            rollback = browser.post('/manage/releases/rollback', json={'request_id':'rollback-one','release_id':record['id']})
            assert rollback.status_code==200, rollback.text
            assert rollback.json()['status']=='rolled_back'
            session = browser.post('/api/session').json()['session_id']
            restored = browser.post('/api/chat',json={'session_id':session,'request_id':'after-rollback','message':question})
            assert restored.status_code==200,restored.text
            assert restored.json()['prompt_version']==1 and generation_versions(restored.json())==['1','1']
            current = browser.get('/manage/releases/status').json()
            assert current['production']['version']==1 and current['comparisons'][0]['eligible']
            assert not any(r['rollback_eligible'] for r in current['records'])
            mutation_count = lambda: len([p for p,b in target['writes'] if '/versions/' in p])
            mutations = mutation_count()
            assert browser.post('/manage/releases/rollback',json={'request_id':'rollback-one','release_id':record['id']}).json()['status']=='rolled_back'
            assert mutation_count()==mutations
            assert browser.post('/manage/releases/promote',json={'request_id':'foreign','comparison_id':'0'*32}).status_code==404
            # Native evidence can disappear after a passing result. It must be rechecked.
            score_id = measured.json()['arms']['candidate']['rows'][-1]['scores'][-1]['id']
            removed = next(s for s in target['scores'] if s['id']==score_id)
            target['scores'].remove(removed)
            incomplete = browser.post('/manage/releases/promote',json={'request_id':'missing-score','comparison_id':identity})
            assert incomplete.status_code==409,incomplete.text
            assert mutation_count()==mutations
            target['scores'].append(removed)
            assert browser.post('/manage/experiments/'+identity+'/readback',json={}).json()['gate']['status']=='passed'
            # Content mismatch and a newer production label invalidate prior eligibility.
            candidate['prompt'] += '\nChanged after comparison.'
            changed = browser.post('/manage/releases/promote',json={'request_id':'changed-content','comparison_id':identity})
            assert changed.status_code==409 and mutation_count()==mutations
            candidate['prompt'] = candidate['prompt'].removesuffix('\nChanged after comparison.')
            client = adapter.langfuse()
            client.api.prompt_version.update(name=prompts.PROMPT_NAME,version=2,new_labels=['production'])
            mutations = mutation_count()
            stale = browser.post('/manage/releases/promote',json={'request_id':'stale-production','comparison_id':identity})
            assert stale.status_code==409 and mutation_count()==mutations
            client.api.prompt_version.update(name=prompts.PROMPT_NAME,version=1,new_labels=['production'])
            mutations = mutation_count()
            target['patch_mode']='denied'
            denied = browser.post('/manage/releases/promote',json={'request_id':'denied-release','comparison_id':identity})
            assert denied.status_code==403,denied.text
            assert mutation_count()==mutations+1
            assert {p.name:p.read_bytes() for p in state.iterdir() if p.is_file()}==original_files
            records = browser.get('/manage/releases/status').json()['records']
            assert records[-1]['status']=='denied' and records[-1]['observed_production']['version']==1
            target.pop('patch_mode')
            assert browser.post('/manage/releases/promote',json={'request_id':'denied-release','comparison_id':identity}).json()['status']=='denied'
            assert mutation_count()==mutations+1
            # The target applies production then loses the response. No blind retry or green state.
            mutations = mutation_count()
            target['patch_mode']='lost'
            lost = browser.post('/manage/releases/promote',json={'request_id':'lost-release','comparison_id':identity})
            assert lost.status_code==502,lost.text
            assert mutation_count()==mutations+1
            target.pop('patch_mode')
            again = browser.post('/manage/releases/promote',json={'request_id':'lost-release','comparison_id':identity})
            assert again.json()['status']=='unconfirmed' and again.json()['observed_production']['version']==2
            assert mutation_count()==mutations+1
