"""Approved delivered-command and browser seams for fixed native banking assets."""
import json
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


def seeded_run(tmp_path, env, target):
    from test_history import api_evidence
    for verb in ('setup-assets','generate-spool','import-spool'):
        result=invoke(tmp_path,verb,extra_env=env)
        assert result.returncode==0,result.stdout+result.stderr
    target.update(api_evidence(tmp_path/'state/events.bound.ndjson'))


def test_fixed_cohorts_keep_expected_outputs_and_exact_current_source_links(tmp_path):
    with asset_target() as (env,target):
        seeded_run(tmp_path,env,target)
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


def test_delivered_history_view_shows_verified_run_links_and_honest_coverage(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from synth.companion.app import create_app,REQUIRES_SECRETS
    from langfuse_synth_core.companion import CompanionAdapter
    from synth.config import load_config
    from northwind import config
    with asset_target() as (env,target):
        seeded_run(tmp_path,env,target)
        assert invoke(tmp_path,'prepare-cohorts',extra_env=env).returncode==0
        result=invoke(tmp_path,'verify','--timeout-seconds','0',extra_env=env)
        assert result.returncode==0,result.stdout+result.stderr
        for key,value in env.items():monkeypatch.setenv(key,value)
        monkeypatch.setenv('SYNTH_STATE_DIR',str(tmp_path/'state'))
        monkeypatch.setenv('LIVE_BASE_PATH','/live/bank-test')
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            langfuse_factory=config.langfuse_factory,native_llm_factory=config.native_llm_factory)
        with TestClient(create_app(adapter)) as browser:
            assert '/live/bank-test/history' in browser.get('/').text
            page=browser.get('/history')
            assert page.status_code==200
            assert 'Historical banking evidence' in page.text
            view=browser.get('/api/history').json()
            assert view['verified'] is True
            assert view['coverage']==dict(total=58,eligible=55,blocked=3,scored=54,unscored=1,pending=2,unknown=3,invalid=1,errors=0)
            assert len(view['cases'])==58 and len(view['datasets'])==6
            from urllib.parse import urlparse, parse_qs
            start,end=map(int,parse_qs(urlparse(view['native_views']['traces']).query)['dateRange'][0].split('-'))
            roots=[r for r in map(json.loads,(tmp_path/'state/events.ndjson').read_text().splitlines()) if r.get('name')=='northwind-assistant']
            assert len(roots)==64
            assert all(start < int(r['startTimeUnixNano'])/1_000_000 < end for r in roots)
            assert view['dates']['trace_count']==64
            assert all('/project/bank-project/traces/' in case['url'] for case in view['cases'])
            assert 'test-secret' not in json.dumps(view) and 'test-public' not in json.dumps(view)
            assert browser.post('/api/history').status_code==405
        with TestClient(create_app(None,preview=True)) as browser:
            preview=browser.get('/api/history').json()
            assert preview['verified'] is False and preview['cases']==[]
            assert 'preview' in preview['status'].lower()


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
