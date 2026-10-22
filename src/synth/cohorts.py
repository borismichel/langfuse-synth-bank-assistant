"""Fixed, source-linked cohorts prepared only after the imported observations exist."""
from __future__ import annotations

import json
import os
import math
import time
from datetime import datetime, timezone
from urllib.parse import quote
from langfuse.api import NotFoundError
from langfuse_synth_core.lfclient import get_langfuse
from langfuse_synth_core.target import TargetProfile
from .assets import (OWNER, REQUEST, corpus, fingerprint, owned_target, save,
                     setup_lock, state_path, definition_matches)
from .materialize import clean
from .receipt import RunReceipt
from .story import validate_anchors


def identities(cfg):
    try:
        receipt = json.loads(state_path('asset-identities.json').read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('Run setup-assets for this target before preparing cohorts') from exc
    owned_target(cfg, receipt)
    if receipt.get('schema_version') != 1 or receipt.get('corpus_hash') != fingerprint(corpus()):
        raise ValueError('Asset identities do not match the shipped fixed corpus')
    return receipt


def read_items(client, name, *, version=None):
    rows = []
    for page in range(1, 6):
        result = client.api.dataset_items.list(dataset_name=name, page=page, limit=100,
            **({'version':version} if version is not None else {}), request_options=REQUEST)
        rows.extend(result.data)
        if result.meta.total_pages > 5:
            raise ValueError(f'Dataset {name} exceeds bounded 500-item scan; no items changed')
        if page >= result.meta.total_pages:
            return {row.id: row for row in rows}
    raise ValueError('Incomplete dataset item scan')


def expected_cohorts(history):
    source = corpus()
    anchors = {c['case_id']: c for c in history.history_anchors['cases']}
    result = {}
    for key, cases in source['cohorts'].items():
        digest = fingerprint(cases)
        name = f'northwind-{key}-{digest[:8]}-{history.history_run_id}'
        metadata = dict(owner=OWNER, schema_version=1, cohort=key, cohort_hash=digest,
            history_run_id=history.history_run_id, upstream_commit=source['upstream_commit'],
            expected_items=len(cases), provenance=source['provenance'])
        items = []
        for case in cases:
            anchor = anchors.get(case['source_case']) if case['source_case'] else None
            if case['source_case'] and anchor is None:
                raise ValueError('Missing mandatory source case ' + case['source_case'])
            items.append(dict(id='nw-' + fingerprint([name,case['id']]), dataset_name=name,
                input=clean(case['input']), expected_output=clean(case['expected_output']), status='ACTIVE',
                source_trace_id=anchor['trace_id'] if anchor else None,
                source_observation_id=anchor['observation_id'] if anchor else None,
                metadata={**clean(case['metadata']), 'owner':OWNER, 'case_id':case['id'], 'cohort':key,
                    'cohort_hash':digest, 'history_run_id':history.history_run_id,
                    'label_provenance':'constructed', 'independent_human_review':False,
                    'source_type':'seeded-observation' if anchor else 'authored-upstream-fixture',
                    'source_revision':source['upstream_commit']}))
        result[key] = dict(name=name, metadata=metadata, items=items)
    return result


def item_matches(row, expected, dataset_id):
    actual = row.model_dump()
    return row.dataset_id == dataset_id and all(actual.get(key) == value for key, value in expected.items())


def check_sources(history, reader):
    anchors = {c['case_id']: c for c in history.history_anchors['cases']}
    sources = {c['source_case'] for cases in corpus()['cohorts'].values() for c in cases if c['source_case']}
    for case_id in sorted(sources):
        expected = anchors[case_id]
        root = next(o for o in expected['observations'] if o['id'] == expected['observation_id'])
        trace = reader.trace(expected['trace_id'], with_scores=False)
        if (trace is None or trace.root is None or trace.root.id != expected['observation_id']
                or trace.input != root['input'] or trace.output != root['output']
                or (trace.metadata or {}).get('history_run_id') != history.history_run_id):
            raise ValueError(f'Missing/mismatched current-run source observation: {case_id}')


def run_prepare_cohorts(cfg, *, timeout_seconds=60):
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError('timeout_seconds must be finite and nonnegative')
    history = RunReceipt.load()
    owned_target(cfg, vars(history))
    validate_anchors(history.history_anchors, history.history_run_id)
    identity = identities(cfg)
    with setup_lock():
        state_path('asset-receipt.json').unlink(missing_ok=True)
        client = get_langfuse(cfg)
        try:
            reader = TargetProfile.detect(cfg.target.base_url).reader(attempts=1)
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    check_sources(history, reader)
                    break
                except ValueError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    print('Waiting for exact current-run source observations before dataset preparation', flush=True)
                    time.sleep(min(2, remaining))
            receipt = dict(schema_version=1, base_url=cfg.target.base_url,
                public_key=os.environ['LANGFUSE_PUBLIC_KEY'], project_id=identity['project_id'],
                history_run_id=history.history_run_id, corpus_hash=identity['corpus_hash'], datasets={})
            project_url = cfg.target.base_url + '/project/' + quote(identity['project_id'], safe='')
            for key, expected in expected_cohorts(history).items():
                name = expected['name']
                try:
                    dataset = client.api.datasets.get(name, request_options=REQUEST)
                except NotFoundError:
                    dataset = client.api.datasets.create(name=name, metadata=expected['metadata'],
                        description='Fixed synthetic banking cohort; constructed reference labels, no independent human review.',
                        request_options=REQUEST)
                if dataset.metadata != expected['metadata']:
                    raise ValueError(f'Dataset ownership/content conflict: {name}; preserved without modification')
                existing = read_items(client, name)
                for item in expected['items']:
                    row = existing.get(item['id'])
                    if row is None:
                        row = client.api.dataset_items.create(**item, request_options=REQUEST)
                    if not item_matches(row, item, dataset.id):
                        raise ValueError(f'Dataset item edited/archived/conflicted: {item["id"]}; human work preserved')
                # Read back all required items before recording a snapshot of their actual server dates.
                actual = read_items(client, name)
                if any(item['id'] not in actual or not item_matches(actual[item['id']], item, dataset.id)
                       for item in expected['items']):
                    raise ValueError(f'Current-run cohort readback incomplete: {key}')
                snapshot = max((actual[item['id']].version or actual[item['id']].updated_at)
                               for item in expected['items']).isoformat()
                receipt['datasets'][key] = dict(id=dataset.id, name=name,
                    url=project_url + '/datasets/' + quote(dataset.id, safe=''), snapshot_at=snapshot,
                    cohort_hash=expected['metadata']['cohort_hash'], item_ids=[item['id'] for item in expected['items']])
                print(f'Prepared {key}: {len(expected["items"])} fixed items; preserved unrelated items', flush=True)
            receipt['prepared_at'] = datetime.now(timezone.utc).isoformat()
            save('asset-receipt.json', receipt)
            add_runbook(cfg, identity, receipt)
            return 0
        finally:
            client.flush()
            client.shutdown()


def add_runbook(cfg, identity, receipt):
    from pathlib import Path
    path = Path(os.environ.get('SYNTH_OUT_DIR', '/app/out')) / 'DEMO_SCRIPT.md'
    source = path.read_text().split('\n\n## Prepared evaluation assets\n', 1)[0]
    links = '\n'.join(f'- [{key}]({ds["url"]}): {len(ds["item_ids"])} fixed items; snapshot `{ds["snapshot_at"]}`; cohort `{ds["cohort_hash"]}`.'
                      for key,ds in receipt['datasets'].items())
    prompts = '\n'.join(f'- [{key}]({p["url"]}): version {p["version"]}.' for key,p in identity['prompts'].items())
    path.write_text(source + '\n\n## Prepared evaluation assets\n\n'
        + f'History run: `{receipt["history_run_id"]}`. Prepared: {receipt["prepared_at"]}. '
        + 'Reference labels are constructed fixtures. No evaluator was activated and no model ran during preparation.\n\n'
        + links + '\n\n### Prepared rubric prompt versions\n\n' + prompts + '\n')


def verify_assets(cfg, history):
    """Read exact prepared identities and frozen dataset snapshots; never mutate target state."""
    identity = identities(cfg)
    receipt = json.loads(state_path('asset-receipt.json').read_text())
    owned_target(cfg, receipt)
    if (receipt.get('history_run_id') != history.history_run_id or receipt.get('corpus_hash') != identity['corpus_hash']
            or receipt.get('schema_version') != 1 or receipt.get('project_id') != identity['project_id']):
        raise ValueError('Missing or mismatched current-run asset receipt')
    expected = expected_cohorts(history)
    if set(receipt['datasets']) != set(expected):
        raise ValueError('Required current-run cohort is absent')
    client = get_langfuse(cfg)
    try:
        source = corpus()
        for key, text in source['prompts'].items():
            bound = identity['prompts'][key]
            prompt = client.get_prompt(bound['name'], version=bound['version'], cache_ttl_seconds=0,
                                       max_retries=0, fetch_timeout_seconds=15)
            if prompt.version != bound['version'] or prompt.prompt != text or OWNER not in prompt.tags:
                raise ValueError(f'Missing/mismatched prepared prompt: {key}')
        for key, definition in source['score_definitions'].items():
            bound = identity['score_definitions'][key]
            row = client.api.score_configs.get_by_id(bound['id'], request_options=REQUEST)
            if not definition_matches(row, definition, bound['description']):
                raise ValueError(f'Missing/mismatched prepared score definition: {key}')
        for key, cohort in expected.items():
            bound = receipt['datasets'][key]
            dataset = client.api.datasets.get(cohort['name'], request_options=REQUEST)
            if (dataset.id != bound['id'] or dataset.metadata != cohort['metadata']
                    or bound['item_ids'] != [item['id'] for item in cohort['items']]):
                raise ValueError(f'Missing/mismatched current-run dataset identity: {key}')
            actual = read_items(client, cohort['name'], version=datetime.fromisoformat(bound['snapshot_at']))
            for item in cohort['items']:
                if item['id'] not in actual or not item_matches(actual[item['id']], item, dataset.id):
                    raise ValueError(f'Missing/mismatched current-run dataset item/source relationship: {key}/{item["metadata"]["case_id"]}')
        check_sources(history, TargetProfile.detect(cfg.target.base_url).reader(attempts=1))
    finally:
        client.flush()
        client.shutdown()
    return receipt
