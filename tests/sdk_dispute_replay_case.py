"""Dispute conversations through SDK, real MCP and delivered presenter HTTP."""
import json
import time
import os
from test_assets import seeded_run
from test_pipeline import invoke


def test_fixture_reply_precedes_owned_confirmation_without_oracle_input(tmp_path, monkeypatch):
    mode = os.environ['BANK_TEST_REPLAY_MODE']
    from fastapi.testclient import TestClient
    from langfuse_synth_core.companion import CompanionAdapter
    from langfuse_synth_core.companion.access import guarded_app
    from synth.config import load_config
    from synth.companion.app import create_app, REQUIRES_SECRETS
    from northwind import config
    from experiment_target import experiment_target, provider_fixture
    with experiment_target() as (env, target):
        seeded_run(tmp_path, env, target)
        assert invoke(tmp_path, 'prepare-cohorts', extra_env=env).returncode == 0
        provider_fixture(env, target, monkeypatch)
        target['dispute_conversation'] = True
        target['dispute_mode'] = mode
        if mode == 'deadline':
            # Time is an external boundary; advance only the runner's clock after
            # the captured first answer, without waiting or affecting SDK timeouts.
            from types import SimpleNamespace
            from synth.companion import experiment_runner
            monkeypatch.setattr(experiment_runner, 'time', SimpleNamespace(
                monotonic=lambda: time.monotonic()+target.get('clock_advance',0)))
        for key, value in env.items(): monkeypatch.setenv(key, value)
        monkeypatch.setenv('SYNTH_STATE_DIR', str(tmp_path/'state'))
        monkeypatch.setenv('PORTAL_LIVE_PROOF', 'a'*64)
        monkeypatch.setenv('PORTAL_LIVE_PROTECTED_PATHS', json.dumps([{'path':'/manage/experiments','access':'manage'}]))
        adapter = CompanionAdapter(load_config('config/demo.yaml'), requires_secrets=REQUIRES_SECRETS,
            llm_model_default=config.AGENT_MODEL, langfuse_factory=config.langfuse_factory,
            native_llm_factory=config.native_llm_factory)
        with TestClient(guarded_app(create_app(adapter), health_path='/api/health')) as browser:
            browser.headers['x-depot-live-proof'] = 'a'*64
            assert browser.post('/manage/experiments/prepare', json={}).status_code == 200
            body = dict(request_id='conversational-dispute', cohort='disputes', baseline_version=1,
                        candidate_version=1, candidate_policy='current', judge_model='fixture-judge', cost_acknowledged=True)
            started = browser.post('/manage/experiments/start', json=body)
            assert started.status_code == 202
            deadline = time.monotonic()+35
            while time.monotonic() < deadline:
                run = next(r for r in browser.get('/manage/experiments/status').json()['runs'] if r['id']==started.json()['id'])
                if run['status'] != 'running': break
                time.sleep(.02)
            if mode in ('deny','deadline','case-limit'):
                assert run['status']=='runner_failed' and run['gate']['status']=='runner_failed',run
                affected=next(r for r in run['arms']['baseline']['rows'] if r['case_id']=='d08')
                assert affected['error'] in ('banking_runner_failed','deadline_reached'),affected
                assert not run['arms']['candidate']['rows']
                app=[c for c in target['calls'] if c['model']!='fixture-judge' and
                     'Quiero disputar el cargo de ELECTROMART' in str(c['messages'][0]['content'])]
                assert len(app)==(10 if mode=='case-limit' else 3)
                assert not any('without executing any further action:' in str(c.get('system')) for c in app)
                before=len(target['calls'])
                assert browser.post('/manage/experiments/start',json=body).json()['id']==run['id']
                assert len(target['calls'])==before
                assert run['limits']['application_dispatches_per_case']==10
                assert run['limits']['total_dispatches']==480 and run['limits']['deadline_seconds']==600
                assert run['limits']['concurrency']==1
                return
            assert run['status'] == 'awaiting_readback', run
            response = browser.post('/manage/experiments/'+run['id']+'/readback', json={})
            assert response.status_code == 200, response.text
            run = response.json()
            for arm in run['arms'].values():
                rows = {r['case_id']:r for r in arm['rows']}
                affected = rows['d08']
                if mode == 'conversation':
                    assert affected['turns'] == 3, affected
                    assert affected['banking_actions'][0]['arguments']['transaction_id'] == 'TX-55300'
                    assert affected['banking_actions'][0]['status'] == 'opened'
                    assert affected['usage']['output'] == 96  # 3 lookup + 4 proposal + 1 report calls, 12 each.
                else:
                    assert affected['turns'] == (2 if mode == 'no-proposal' else 1), affected
                    assert affected['checks']['dispute-correct-transaction'] == 0
                    assert not any(a.get('status') == 'opened' for a in affected['banking_actions'])
                assert all(r['turns'] == 2 for key,r in rows.items() if key != 'd08')
                assert all(r['verified'] for r in rows.values())
            assert run['gate']['status'] == ('passed' if mode == 'conversation' else 'failed'), run['gate']
            assert run['limits'] == dict(cases_per_arm=16, arms=2, application_dispatches_per_case=10,
                judge_calls_per_case=5, total_dispatches=480, deadline_seconds=600, concurrency=1, judge_concurrency=2, scheduler='serial-application-two-judges-v1')
            assert run['dispatches'] == len(target['calls']) == len(target['permits'])
            # Provider HTTP bodies expose what the app actually received; oracle metadata
            # must never be inserted into its messages or system prompt.
            application = [c for c in target['calls'] if c['model'] != 'fixture-judge']
            assert all('expected_transaction' not in json.dumps(c) and 'accepted_transactions' not in json.dumps(c)
                       for c in application)
            fixture_replies={r['input']['question']:r['metadata'].get('confirm_reply')
                             for r in target['items'].values() if r.get('metadata',{}).get('cohort')=='disputes'}
            for call in application:
                human=[]
                for message in call['messages']:
                    if message['role']!='user':continue
                    content=message['content']
                    if isinstance(content,str):human.append(content)
                    elif all(block.get('type')=='text' for block in content):
                        human.append(''.join(block['text'] for block in content))
                assert human[0] in fixture_replies and 1<=len(human)<=3
                assert human[1:]==[fixture_replies[human[0]]]*(len(human)-1)
            measured = {'input':sum(30+len(json.dumps(c['messages']))//4 for c in application),
                        'output':12*len(application)}
            assert {k:sum(r['usage'][k] for a in run['arms'].values() for r in a['rows'])
                    for k in ('input','output')} == measured
            if mode == 'conversation':
                conversational = [c for c in application if any(m['role']=='assistant' and
                    isinstance(m['content'],str) and 'ELECTROMART' in m['content'] for m in c['messages'])]
                assert conversational
                assert any(len([m for m in c['messages'] if m['role']=='user']) >= 3
                           for c in conversational)  # Initial question, fixture reply, explicit confirmation.
            calls = len(target['calls'])
            assert browser.post('/manage/experiments/start', json=body).json()['id'] == run['id']
            assert len(target['calls']) == calls
