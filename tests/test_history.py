"""Historical story assertions through the delivered generation command."""
import json
from test_pipeline import invoke


def attrs(span):
    return {a['key']: a['value'].get('stringValue', a['value'].get('intValue'))
            for a in span.get('attributes', [])}


def test_complete_history_keeps_heroes_and_confirmation_sessions(tmp_path):
    result = invoke(tmp_path, 'generate-spool', '--set', 'generation.target_traces=64',
                    '--set', 'generation.as_of_date=2026-10-20')
    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in (tmp_path / 'state/events.ndjson').read_text().splitlines()]
    roots = {attrs(e).get('langfuse.trace.metadata.case_id'): e for e in lines
             if e.get('name') == 'northwind-assistant'}
    assert len(roots) == 64
    mandatory = {'fees-en-1', 'fees-en-2', 'fees-es', 'lost-card-en-request',
                 'lost-card-en-confirm', 'lost-card-es-request', 'lost-card-es-confirm',
                 'dispute-request', 'dispute-confirm', 'dispute-window-en-failure',
                 'dispute-window-es-recovery', 'cost-failure', 'cost-recovery',
                 'injection-en', 'injection-es', 'masked-card-en', 'masked-card-es',
                 'spanish-register-failure', 'spanish-register-control', 'failed-action',
                 'noop-action', 'conduct-p1-advice', 'conduct-p2-card-request',
                 'conduct-p3-soft', 'conduct-p4-missing', 'conduct-p5-insured-fund',
                 'missing-verdict', 'invalid-verdict', 'fail-plus-unknown', 'unscored'}
    assert mandatory <= roots.keys()
    for before, after in [('lost-card-en-request', 'lost-card-en-confirm'),
                          ('lost-card-es-request', 'lost-card-es-confirm'),
                          ('dispute-request', 'dispute-confirm')]:
        a, b = roots[before], roots[after]
        assert attrs(a)['langfuse.session.id'] == attrs(b)['langfuse.session.id']
        assert int(a['startTimeUnixNano']) < int(b['startTimeUnixNano'])
        before_tools = [e['name'] for e in lines if e.get('traceId') == a['traceId']]
        assert not any(name in before_tools for name in ['mcp: block_card', 'mcp: open_dispute'])
    assert '4111 1111 1111 1111' not in json.dumps(lines)
    assert 'REDACTED_CARD' in json.dumps(lines)


def test_history_prompt_links_resolve_actual_versions_after_target_setup(tmp_path):
    from test_pipeline import target_server
    with target_server() as (env, _):
        setup = invoke(tmp_path, 'setup-prompts', extra_env=env)
        assert setup.returncode == 0, setup.stderr
        generated = invoke(tmp_path, 'generate-spool', extra_env=env)
        assert generated.returncode == 0, generated.stderr
        original = (tmp_path / 'state/events.ndjson').read_bytes()
        imported = invoke(tmp_path, 'import-spool', extra_env=env)
        assert imported.returncode == 0, imported.stderr
        assert (tmp_path / 'state/events.ndjson').read_bytes() == original
        receipt = json.loads((tmp_path / 'state/.synth_verify.json').read_text())
        generations = [o for c in receipt['history_anchors']['cases'] for o in c['observations'] if o['type']=='GENERATION']
        assert generations and all(o['prompt_version'] == 7 for o in generations)
        assert all(o['prompt_name'].startswith('northwind-history-') for o in generations)
        assert not any(attrs(e).get('langfuse.observation.prompt.name') for e in map(json.loads, original.splitlines()))


def api_evidence(spool):
    """Local target projection of shipped OTLP and v3 typed score contracts."""
    from datetime import datetime, timezone
    result = {'observations': [], 'scores': []}
    for row in map(json.loads, spool.read_text().splitlines()):
        if row.get('type') == 'score-create':
            body = row['body']
            result['scores'].append({**body, 'source': 'API', 'timestamp': row['timestamp'],
                'subject': {'kind':'observation', 'id':body['observationId'], 'traceId':body['traceId']}})
            continue
        a = attrs(row)
        metadata = {}
        for key, value in a.items():
            if '.metadata.' in key:
                name = key.split('.metadata.',1)[1]
                try:
                    metadata[name] = json.loads(value)
                except (ValueError, TypeError):
                    metadata[name] = value
        result['observations'].append(dict(id=row['spanId'], traceId=row['traceId'],
            parentObservationId=row.get('parentSpanId'), type=a['langfuse.observation.type'].upper(),
            providedModelName=a.get('langfuse.observation.model.name'),
            usageDetails=json.loads(a.get('langfuse.observation.usage_details','null')),
            totalCost=json.loads(a.get('langfuse.observation.cost_details','{}')).get('total'),
            name=row['name'], startTime=datetime.fromtimestamp(int(row['startTimeUnixNano'])/1e9,timezone.utc).isoformat(),
            input=a.get('langfuse.observation.input'), output=a.get('langfuse.observation.output'),
            metadata=metadata, environment=a['langfuse.environment'], sessionId=a['langfuse.session.id'],
            promptName=a.get('langfuse.observation.prompt.name'),
            promptVersion=int(a['langfuse.observation.prompt.version']) if 'langfuse.observation.prompt.version' in a else None))
    return result


def test_verify_requires_every_current_hero_and_real_api_score_source(tmp_path):
    from test_pipeline import target_server
    evidence = {'observations': [], 'scores': []}
    with target_server(evidence=evidence) as (env, _):
        assert invoke(tmp_path, 'setup-prompts', extra_env=env).returncode == 0
        assert invoke(tmp_path, 'generate-spool', extra_env=env).returncode == 0
        imported = invoke(tmp_path, 'import-spool', extra_env=env)
        assert imported.returncode == 0, imported.stderr
        evidence.update(api_evidence(tmp_path / 'state/events.bound.ndjson'))
        verified = invoke(tmp_path, 'verify', '--timeout-seconds', '0', extra_env=env)
        assert verified.returncode == 0, verified.stdout + verified.stderr
        # Even complete same-name/case evidence from an old run cannot fill a current gap.
        missing = next(o for o in evidence['observations'] if o['name']=='northwind-assistant' and
                       o['metadata']['case_id']=='lost-card-es-confirm')['traceId']
        for row in evidence['observations']:
            if row['traceId'] == missing:
                row['traceId'] = '0' * 32
        verified = invoke(tmp_path, 'verify', '--timeout-seconds', '0', extra_env=env)
        assert verified.returncode == 1
        assert 'lost-card-es-confirm: missing trace' in verified.stdout
        evidence.update(api_evidence(tmp_path / 'state/events.bound.ndjson'))
        evidence['scores'][0]['source'] = 'EVAL'
        verified = invoke(tmp_path, 'verify', '--timeout-seconds', '0', extra_env=env)
        assert verified.returncode == 1
        assert 'historical API score' in verified.stdout


def test_frozen_conduct_labels_preserve_unknown_missing_and_known_failure(tmp_path):
    result = invoke(tmp_path, 'generate-spool')
    assert result.returncode == 0, result.stderr
    lines = list(map(json.loads, (tmp_path / 'state/events.ndjson').read_text().splitlines()))
    roots = {attrs(e).get('langfuse.trace.metadata.case_id'):e for e in lines if e.get('name')=='northwind-assistant'}
    def scores(case):
        return {e['body']['name']:e['body'] for e in lines if e.get('type')=='score-create' and e['body']['traceId']==roots[case]['traceId']}
    for case in ('missing-verdict', 'invalid-verdict', 'conduct-p4-missing', 'conduct-p4-history'):
        assert 'historical-compliance' not in scores(case)
    assert scores('fail-plus-unknown')['historical-compliance']['value']==0
    assert scores('fail-plus-unknown')['historical-compliance-status']['value']=='violation'
    assert scores('conduct-p3-soft')['historical-compliance']['value']==0.5
    for case, policy in [('conduct-p1-advice','p1'),('conduct-p2-pin','p2'),('conduct-p3-pressure','p3'),('conduct-p4-refund','p4'),('conduct-p5-insured-fund','p5')]:
        assert scores(case)['historical-policy-'+policy]['value']=='fail'
    assert scores('unscored') == {}
    context = json.loads(attrs(roots['conduct-p4-history'])['langfuse.trace.metadata.context'])
    assert context['authoritative_policy']==''
    assert 'two business days' in json.dumps(context['prior_conversation_non_authoritative'])
    for case in roots:
        for body in scores(case).values():
            assert body['observationId']==roots[case]['spanId']
            assert body['environment']=='synthetic-history'
            assert 'not managed EVAL' in body['comment']


def test_scaling_and_retargeting_preserve_all_mandatory_turns(tmp_path):
    snapshots = {}
    for name, count, host in [('minimum',58,'https://target-a.example'),('repeat',58,'https://target-b.example'),('larger',256,'https://target-b.example')]:
        path = tmp_path / name
        result = invoke(path, 'generate-spool', '--set', f'generation.target_traces={count}',
                        '--set', 'generation.as_of_date=2026-10-20', extra_env={'LANGFUSE_BASE_URL':host})
        assert result.returncode == 0, result.stderr
        snapshots[name] = (path / 'state/events.ndjson').read_bytes()
    assert snapshots['minimum'] == snapshots['repeat']
    # Background growth appends records; it never changes any mandatory event or cohort.
    assert snapshots['larger'].startswith(snapshots['minimum'])
    changed = invoke(tmp_path / 'later', 'generate-spool', '--set', 'generation.target_traces=58',
                     '--set', 'generation.as_of_date=2026-10-21')
    assert changed.returncode == 0, changed.stderr
    first = json.loads(snapshots['minimum'].splitlines()[0])['traceId']
    assert json.loads((tmp_path / 'later/state/events.ndjson').read_text().splitlines()[0])['traceId'] != first


def test_regression_and_control_evidence_keeps_real_tool_outcomes(tmp_path):
    result = invoke(tmp_path, 'generate-spool')
    assert result.returncode == 0, result.stderr
    rows = list(map(json.loads, (tmp_path / 'state/events.ndjson').read_text().splitlines()))
    roots = {attrs(e).get('langfuse.trace.metadata.case_id'):e for e in rows if e.get('name')=='northwind-assistant'}
    def observations(case, name):
        return [attrs(e) for e in rows if e.get('traceId')==roots[case]['traceId'] and e.get('name')==name]
    for lang in ('en','es'):
        for beat, days in [('baseline',60),('failure',30),('recovery',60)]:
            tool = observations(f'dispute-window-{lang}-{beat}', 'mcp: list_transactions')[0]
            assert json.loads(tool['langfuse.observation.input'])['days']==days
            assert bool(json.loads(tool['langfuse.observation.output'])['transactions']) == (beat!='failure')
        control = observations('recent-dispute-control-'+lang, 'mcp: list_transactions')[0]
        assert json.loads(control['langfuse.observation.input'])['days']==30
        assert json.loads(control['langfuse.observation.output'])['transactions']
    for beat, count in [('baseline',2),('failure',8),('recovery',2)]:
        retrieval = observations('cost-'+beat, 'kb-retrieval')[0]
        assert len(json.loads(retrieval['langfuse.observation.output']))==count
    cost = lambda beat: sum(json.loads(o['langfuse.observation.cost_details'])['total'] for o in observations('cost-'+beat,'generate-response'))
    assert cost('failure') > cost('baseline') == cost('recovery')
    for case in ('failed-action','noop-action'):
        tool = observations(case, 'mcp: block_card')[0]
        assert json.loads(tool['langfuse.observation.output'])['success'] is False
    for case in ('lost-card-en-confirm','lost-card-es-confirm','dispute-confirm'):
        final = observations(case,'generate-response')[-1]
        messages = json.loads(final['langfuse.observation.input'])
        assert sum(m['role']=='user' for m in messages)==2
        call_ids = {c['id'] for m in messages for c in m.get('tool_calls',[])}
        assert call_ids == {m['tool_call_id'] for m in messages if m['role']=='tool'}
