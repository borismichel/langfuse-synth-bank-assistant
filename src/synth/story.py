"""Current-run history anchors and evidence checks, using core's read seam."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from langfuse_synth_core.timegen import iso
from .materialize import fixture


def attributes(event):
    def value(v):
        if 'intValue' in v:
            return int(v['intValue'])
        if 'arrayValue' in v:
            return [value(item) for item in v['arrayValue']['values']]
        return next(iter(v.values()))
    return {item['key']: value(item['value']) for item in event.get('attributes', [])}


def decoded(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def history_anchors(events):
    """Bounded, versioned expectations for every mandatory turn, never background rows."""
    required = {c['id'] for c in fixture()['cases']}
    cases = {}
    for event in events:
        if event.get('name') != 'northwind-assistant':
            continue
        a = attributes(event)
        case_id = a['langfuse.trace.metadata.case_id']
        if case_id not in required:
            continue
        cases[event['traceId']] = dict(case_id=case_id, trace_id=event['traceId'],
            observation_id=event['spanId'], session_id=a['langfuse.session.id'],
            timestamp=iso(datetime.fromtimestamp(int(event['startTimeUnixNano']) / 1e9, timezone.utc)),
            history_run_id=a['langfuse.trace.metadata.history_run_id'],
            observations=[], scores=[])
    for event in events:
        trace_id = event.get('traceId') or event.get('body', {}).get('traceId')
        if trace_id not in cases:
            continue
        if event.get('type') == 'score-create':
            cases[trace_id]['scores'].append({**event['body'], 'timestamp': event['timestamp']})
        elif 'spanId' in event:
            a = attributes(event)
            cases[trace_id]['observations'].append(dict(id=event['spanId'], parent_id=event.get('parentSpanId'),
                name=event['name'], type=a['langfuse.observation.type'].upper(),
                input=decoded(a.get('langfuse.observation.input')), output=decoded(a.get('langfuse.observation.output')),
                prompt_name=a.get('langfuse.observation.prompt.name'), prompt_version=a.get('langfuse.observation.prompt.version'),
                model=a.get('langfuse.observation.model.name'),
                usage_details=decoded(a.get('langfuse.observation.usage_details')),
                total_cost=(decoded(a.get('langfuse.observation.cost_details')) or {}).get('total')))
    return {'schema_version': 1, 'cases': list(cases.values())}


def validate_anchors(anchors, run_id):
    if anchors.get('schema_version') != 1:
        raise ValueError('Unsupported history anchor schema')
    cases = anchors.get('cases', [])
    required = {c['id'] for c in fixture()['cases']}
    if len(cases) != len(required) or {c['case_id'] for c in cases} != required:
        raise ValueError('Receipt does not contain every mandatory history case')
    if any(c['history_run_id'] != run_id or not c['observations'] or not isinstance(c['scores'], list) for c in cases):
        raise ValueError('Incomplete or mixed-run history anchors')


def check_case(reader, case):
    trace_id = case['trace_id']
    trace = reader.trace(trace_id, with_scores=False)
    if (trace is None or trace.timestamp is None or iso(trace.timestamp) != case['timestamp']
            or trace.session_id != case['session_id'] or trace.environment != 'synthetic-history'
            or trace.root is None or trace.root.id != case['observation_id']):
        return False, f'missing trace {trace_id} or mismatched current-run timestamp/session/root'
    # Native context must remain synthetic and must not be eligible for the live EVAL reducer.
    metadata = trace.metadata or {}
    if (metadata.get('case_id') != case['case_id'] or metadata.get('history_run_id') != case['history_run_id']
            or metadata.get('evaluation_provenance') != 'synthetic-api-fixture'):
        return False, 'missing or mismatched synthetic history provenance'
    observations = {o.id: o for o in trace.observations}
    for expected in case['observations']:
        observation = observations.get(expected['id'])
        if observation is None:
            return False, f'missing current-run observation {expected["name"]}'
        for key in ('parent_id', 'name', 'type', 'input', 'output', 'prompt_name', 'prompt_version', 'model', 'usage_details'):
            actual, wanted = getattr(observation, key), expected[key]
            # Target4.68 returns {} when no usage was supplied. Nonempty usage stays exact.
            if key == 'usage_details' and actual == {} and wanted is None:
                continue
            if actual != wanted:
                return False, f'mismatched {expected["name"]} {key}'
        if expected['total_cost'] is not None and (observation.total_cost is None or
                abs(observation.total_cost - expected['total_cost']) > 1e-9):
            return False, f'mismatched {expected["name"]} synthetic cost'
    scores = {s.id: s for s in reader.scores(trace_id=trace_id, limit_pages=1)}
    expected_ids = {s['id'] for s in case['scores']}
    if any(s.id not in expected_ids and (s.name.startswith('historical-') or s.name == 'policy-correctness')
           for s in scores.values()):
        return False, 'unexpected historical score (unresolved cases must not acquire a numeric headline)'
    for expected in case['scores']:
        score = scores.get(expected['id'])
        if (score is None or score.trace_id != trace_id or score.observation_id != case['observation_id']
                or score.name != expected['name'] or score.value != expected['value']
                or score.data_type != expected['dataType'] or score.source != 'API'
                or score.environment != 'synthetic-history' or score.timestamp is None
                or iso(score.timestamp) != expected['timestamp']):
            return False, f'missing/mismatched historical API score {expected["name"]} and delivered-answer subject'
    return True, 'current-run turn, observation tree, prompt associations and API fixture scores visible'
