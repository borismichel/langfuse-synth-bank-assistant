"""Approved delivered-command and browser seams for fixed native banking assets."""
import json
import shutil
from asset_target import asset_target
from test_pipeline import invoke


def test_preparation_creates_owned_definitions_and_prompts_without_paid_evaluation(tmp_path):
    with asset_target() as (env,target):
        first=invoke(tmp_path,'setup-assets',extra_env=env)
        assert first.returncode==0,first.stderr
        writes=len(target['writes'])
        second=invoke(tmp_path,'setup-assets',extra_env=env)
        assert second.returncode==0,second.stderr
        assert len(target['writes'])==writes
        assert len(target['definitions'])>=10
        assert any('conduct-v2-p4' in name for name in target['prompts'])
        assert {path for path,_ in target['writes']} <= {'/api/public/v2/prompts','/api/public/score-configs'}
        receipt=json.loads((tmp_path/'state/asset-identities.json').read_text())
        assert receipt['project_id']=='bank-project'
        assert all(p['version']==7 for p in receipt['prompts'].values())


def seeded_run(tmp_path, env, target, *, generation_args=()):
    from test_history import api_evidence
    for verb in ('setup-assets','generate-spool','import-spool'):
        result=invoke(tmp_path,verb,*(generation_args if verb=='generate-spool' else ()),extra_env=env)
        assert result.returncode==0,result.stdout+result.stderr
    target.update(api_evidence(tmp_path/'state/events.bound.ndjson'))


def test_fixed_cohorts_keep_expected_outputs_and_exact_current_source_links(tmp_path):
    with asset_target() as (env,target):
        seeded_run(tmp_path,env,target)
        # Depot carries state across jobs, but each step gets a fresh artifact output.
        shutil.rmtree(tmp_path/'out')
        result=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert result.returncode==0,result.stdout+result.stderr
        receipt=json.loads((tmp_path/'state/asset-receipt.json').read_text())
        assert {key:len(value['item_ids']) for key,value in receipt['datasets'].items()} == {
            'english':16,'spanish':10,'redteam':10,'calibration':16,'conduct':22,'disputes':10}
        conduct=[item for item in target['items'].values() if item['metadata']['cohort']=='conduct']
        assert len(conduct)==22
        assert all(item['sourceObservationId'] and item['sourceTraceId'] for item in conduct)
        assert all(item['metadata']['label_provenance']=='constructed' for item in conduct)
        pin=next(item for item in conduct if item['metadata']['case_id']=='p2-pin')
        assert pin['expectedOutput']['P2']=='fail'
        assert 'expected' not in pin['input']
        writes=len(target['writes'])
        result=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert result.returncode==0,result.stdout+result.stderr
        assert len(target['writes'])==writes
        assert 'dataset-' in (tmp_path/'out/DEMO_SCRIPT.md').read_text()
        shutil.rmtree(tmp_path/'out')
        verified=invoke(tmp_path,'verify','--timeout-seconds','0',extra_env=env)
        assert verified.returncode==0,verified.stdout+verified.stderr
        runbook=(tmp_path/'out/DEMO_SCRIPT.md').read_text()
        assert 'Prepared evaluation assets' in runbook and 'Verified history view' in runbook
        assert 'dataset-' in runbook and '/traces/' in runbook
        assert receipt['history_run_id'] in runbook
        # The downloaded primary artifact carries the delivered option instructions;
        # it must not depend on browsing this checkout's relative documentation.
        assert '64 MiB budget' in runbook
        assert 'Jaeger' in runbook and 'n8n' in runbook
        assert 'Coding-agent workshop' in runbook
        assert '## Governance qualification' in runbook
        assert 'Saved dataset items and audit logs' in runbook


def test_conflicting_human_item_is_preserved_and_missing_current_asset_fails_verify(tmp_path):
    with asset_target() as (env,target):
        seeded_run(tmp_path,env,target)
        assert invoke(tmp_path,'prepare-cohorts',extra_env=env).returncode==0
        verified=invoke(tmp_path,'verify','--timeout-seconds','0',extra_env=env)
        assert verified.returncode==0,verified.stdout+verified.stderr
        item=next(iter(target['items'].values()))
        old=item['expectedOutput']
        item['expectedOutput']='Human reviewed correction'
        writes=len(target['writes'])
        prepared=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert prepared.returncode==1
        assert 'human work preserved' in prepared.stderr
        assert item['expectedOutput']=='Human reviewed correction'
        assert len(target['writes'])==writes
        item['expectedOutput']=old
        assert invoke(tmp_path,'prepare-cohorts',extra_env=env).returncode==0
        missing=next(iter(target['items']))
        replaced=target['items'].pop(missing)
        target['items']['old-similar-item']={**replaced,'id':'old-similar-item'}
        verified=invoke(tmp_path,'verify','--timeout-seconds','0',extra_env=env)
        assert verified.returncode==1
        assert 'dataset item/source relationship' in verified.stdout


def test_delivered_history_view_shows_verified_run_links_and_honest_coverage(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    # Real MCP startup belongs to an isolated, credential-clean HTTP fixture
    # process, like the other delivered Companion contracts. The authoring
    # worker deliberately denies even loopback connections in this process.
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'LANG')}
    env['PYTHONPATH'] = os.pathsep.join(['src', 'tests', 'demos/bank-assistant'])
    process = subprocess.Popen([sys.executable, 'tests/sdk_history_case.py', str(tmp_path)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        output, errors = process.communicate(timeout=45)
    finally:
        # MCP inherits the fixture's output pipes. Kill only this owned process
        # group, including that child, if startup/teardown ever stalls again.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    assert process.returncode == 0, output + errors
    assert 'history_contract_passed' in output


def test_asset_preparation_preserves_unrelated_resources_and_rejects_ambiguous_names(tmp_path):
    import copy
    with asset_target() as (env,target):
        assert invoke(tmp_path,'setup-assets',extra_env=env).returncode==0
        definition=next(iter(target['definitions'].values()))
        other={**copy.deepcopy(definition),'id':'human-definition','name':'Human review dimension','description':'Reviewer owned'}
        target['definitions'][other['id']]=other
        before=copy.deepcopy(other)
        writes=len(target['writes'])
        assert invoke(tmp_path,'setup-assets',extra_env=env).returncode==0
        assert target['definitions']['human-definition']==before
        assert len(target['writes'])==writes
        target['definitions']['ambiguous']={**copy.deepcopy(definition),'id':'ambiguous'}
        result=invoke(tmp_path,'setup-assets',extra_env=env)
        assert result.returncode==1 and 'Ambiguous score definition' in result.stderr
        assert len(target['writes'])==writes


def test_fixed_dataset_cohorts_and_lineage_do_not_scale_with_background_traffic(tmp_path):
    snapshots=[]
    for count in (58,256):
        with asset_target() as (env,target):
            path=tmp_path/str(count)
            assert invoke(path,'setup-assets',extra_env=env).returncode==0
            generated=invoke(path,'generate-spool','--set',f'generation.target_traces={count}',
                             '--set','generation.as_of_date=2026-10-22',extra_env=env)
            assert generated.returncode==0,generated.stderr
            assert invoke(path,'import-spool',extra_env=env).returncode==0
            from test_history import api_evidence
            target.update(api_evidence(path/'state/events.bound.ndjson'))
            prepared=invoke(path,'prepare-cohorts',extra_env=env)
            assert prepared.returncode==0,prepared.stderr
            snapshots.append({key:{field:value for field,value in row.items() if field not in
                {'createdAt','updatedAt','version','datasetId'}} for key,row in target['items'].items()})
    assert snapshots[0]==snapshots[1]


def test_cohort_setup_requires_visible_current_source_before_any_dataset_write(tmp_path):
    with asset_target() as (env,target):
        seeded_run(tmp_path,env,target)
        target['observations']=[]
        before=len(target['writes'])
        result=invoke(tmp_path,'prepare-cohorts','--timeout-seconds','0',extra_env=env)
        assert result.returncode==1
        assert 'source observation' in result.stderr
        assert len(target['writes'])==before
        assert not (tmp_path/'state/asset-receipt.json').exists()


def test_calibration_numeric_labels_round_trip_and_changed_labels_remain_conflicts(tmp_path):
    with asset_target() as (env,target):
        seeded_run(tmp_path,env,target)
        prepared=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert prepared.returncode==0,prepared.stdout+prepared.stderr
        items={r['metadata']['case_id']:r for r in target['items'].values() if r['metadata']['cohort']=='calibration'}
        assert items['f01']['expectedOutput']==1.0
        assert items['m01']['expectedOutput']==.5
        assert items['e01']['expectedOutput']==0.0
        sent=[b for path,b in target['writes'] if path.endswith('/dataset-items') and b['metadata']['cohort']=='calibration']
        assert all(type(b['expectedOutput']) in (float,int) for b in sent)
        before=len(target['writes'])
        assert invoke(tmp_path,'prepare-cohorts',extra_env=env).returncode==0
        assert len(target['writes'])==before
        items['f01']['expectedOutput']=.5
        conflict=invoke(tmp_path,'prepare-cohorts',extra_env=env)
        assert conflict.returncode==1 and 'human work preserved' in conflict.stderr
        assert items['f01']['expectedOutput']==.5 and len(target['writes'])==before
