"""Delivered history HTTP contract in the same isolated seam as live SDK tests."""
import json
import os
import sys
from pathlib import Path
from asset_target import asset_target
from test_assets import seeded_run
from test_pipeline import invoke


def run(tmp_path):
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
        os.environ.update(env)
        os.environ['SYNTH_STATE_DIR']=str(tmp_path/'state')
        os.environ['LIVE_BASE_PATH']='/live/bank-test'
        adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,
            langfuse_factory=config.langfuse_factory,native_llm_factory=config.native_llm_factory)
        with TestClient(create_app(adapter)) as browser:
            assert browser.get('/api/health').json()['banking_ready'] is True
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


if __name__ == '__main__':
    run(Path(sys.argv[1]))
    print('history_contract_passed')
