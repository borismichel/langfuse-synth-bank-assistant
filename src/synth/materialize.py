"""Frozen, target-independent banking history. No models, clocks or network calls."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from langfuse_synth_core.rng import Rng
from langfuse_synth_core.seed.events import generation_event, observation_event, score_event, trace_event
from langfuse_synth_core.seed.otlp import trace_root_span_id
from northwind.masking import scrub

FIXTURE_PATH = Path(__file__).with_name('history.json')
HERO_COUNT = 58
MAX_TRACES = 256


def fixture():
    return json.loads(FIXTURE_PATH.read_text())


def cases_for_count(count):
    cases = copy.deepcopy(fixture()['cases'])
    if not HERO_COUNT <= count <= MAX_TRACES:
        raise ValueError(f'Banking history requires {HERO_COUNT}–{MAX_TRACES} traces')
    if len(cases) != HERO_COUNT:
        raise ValueError('Fixture count changed; review the declared story floor')
    for i in range(count - HERO_COUNT):
        case = copy.deepcopy(cases[0 if i % 2 == 0 else 2])
        case.update(id=f'background-{i:06d}', group=f'background-{i:06d}', cohort='background')
        cases.append(case)
    return cases


def clean(value):
    if isinstance(value, str):
        return scrub(value)[0]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def run_identity(seed, run_date, history_days=7):
    digest = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    return hashlib.sha256(f'bank-history-v2:{seed}:{run_date.isoformat()}:{history_days}:{digest}'.encode()).hexdigest()[:24]


def build_events(target_traces: int, params: Mapping[str, Any], *, run_date: datetime, progress=None) -> list[dict]:
    corpus = fixture()
    history_days = int(params.get('history_days', 7))
    run_id = run_identity(int(params.get('seed', 42)), run_date, history_days)
    rng = Rng(int(params.get('seed', 42))).sub('bank-history', run_id)
    docs = {doc['id']: doc for doc in corpus['knowledge']}
    events, history = [], {}
    for index, raw in enumerate(cases_for_count(target_traces)):
        if progress and index % 16 == 0:
            progress(f'Materializing turn {index + 1}/{target_traces}')
        case = clean(raw)
        r = rng.sub(case['id'])
        trace_id = r.trace_id(0)
        subject = trace_root_span_id(trace_id)
        group = case['group']
        prior = history.setdefault(group, [])
        session = 'bank-history-' + rng.sub('session', group).trace_id(0)
        day = index * (history_days - 1) // 57 if index < 58 else (index - 58) % history_days
        ts = run_date.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=history_days - 1 - day) + timedelta(seconds=index * 60)
        authority = case.get('authority_override')
        if authority is None:
            authority = '\n'.join(f"[{key}] {docs[key]['text']}" for key in case['docs'])
        context = {'authoritative_policy': authority, 'tool_results': case['tools'],
                   'prior_conversation_non_authoritative': case.get('prior_claims_fixture', copy.deepcopy(prior))}
        metadata = dict(case_id=case['id'], history_run_id=run_id, fixture_version=1,
                        evidence='seeded-fixture', evaluation_provenance='synthetic-api-fixture',
                        eligible_live_evaluation=False, cohort=case['cohort'],
                        upstream_commit=corpus['upstream_commit'], customer_id=case['customer'],
                        language=case['language'], release=case['release'], prompt_key=case['prompt'],
                        comparison=case.get('comparison', ''), beat=case.get('beat', ''),
                        context=context, turn=len(prior) // 2 + 1)
        if raw != case:
            metadata['pii_redacted'] = True
        common = dict(trace_id=trace_id, environment='synthetic-history')
        events.append(trace_event(**common, timestamp=ts, name='northwind-assistant',
            user_id=case['customer'], session_id=session, tags=['seeded-fixture', 'northwind-history', case['cohort']],
            metadata=metadata, input=case['query'], output=case['answer']))
        chain = r.obs_id('orchestration')
        events.append(observation_event(**common, obs_id=chain, name='northwind-orchestration',
            obs_type='chain', start=ts, end=ts + timedelta(seconds=3), parent_id=subject,
            input=case['query'], output=case['answer']))
        events.append(observation_event(**common, obs_id=r.obs_id('input-guardrail'), name='input-guardrail',
            obs_type='guardrail', start=ts, end=ts + timedelta(milliseconds=10), parent_id=chain,
            input=case['query'], output={'blocked': case['blocked'], 'fixture': True}))
        messages = [{'role':'system','content':corpus['prompts'][case['prompt']].replace('{{bank_name}}', 'Northwind Community Bank')}] + copy.deepcopy(prior) + [{'role':'user','content':case['query']}]
        tools = copy.deepcopy(case['tools'])
        if case['docs']:
            tools.insert(0, dict(name='search_knowledge_base', args={'query':case['query'], 'k':case.get('retrieval_k',3), 'min_score':case.get('min_score',0.08)},
                result=[{**docs[key], 'relevance':0.3 if n < 2 else 0.04} for n,key in enumerate(case['docs'])]))
        binding = params.get('prompt_bindings', {}).get(case['prompt'], {})
        def generation(key, incoming, outgoing, tokens, offset):
            events.append(generation_event(**common, obs_id=r.obs_id(key), parent_id=chain,
                name='generate-response', start=ts+timedelta(milliseconds=offset),
                end=ts+timedelta(milliseconds=offset+500), model='fixture-bank-model',
                input=incoming, output=outgoing, usage_details={'input':tokens,'output':case['output_tokens'],'total':tokens+case['output_tokens']},
                cost_details={'total':round(tokens * 0.000003 + case['output_tokens'] * 0.000015, 6)},
                metadata={'evidence':'seeded-fixture','prompt_key':case['prompt'], 'cost_provenance':'synthetic-cost-fixture'},
                prompt_name=binding.get('name'), prompt_version=binding.get('version')))
        if not case['blocked']:
            if tools:
                calls = [{'id':f'call-{n}', 'type':'function','function':{'name':t['name'],'arguments':json.dumps(t['args'],ensure_ascii=False)}} for n,t in enumerate(tools)]
                selection = {'role':'assistant','content':'','tool_calls':calls}
                generation('select-tools', messages, selection, 1142, 20)
                messages += [selection]
                for n,t in enumerate(tools):
                    start=ts+timedelta(milliseconds=600+n*60)
                    parent=r.obs_id(f'tool-{n}')
                    events.append(observation_event(**common, obs_id=parent, parent_id=chain,
                        name=t['name'] if t['name']=='search_knowledge_base' else 'mcp-client: '+t['name'],
                        obs_type='tool' if t['name']=='search_knowledge_base' else 'span', start=start,
                        end=start+timedelta(milliseconds=50), input=t['args'], output=t['result']))
                    events.append(observation_event(**common, obs_id=r.obs_id(f'evidence-{n}'), parent_id=parent,
                        name='kb-retrieval' if t['name']=='search_knowledge_base' else 'mcp: '+t['name'],
                        obs_type='retriever' if t['name']=='search_knowledge_base' else 'tool', start=start,
                        end=start+timedelta(milliseconds=40), input=t['args'], output=t['result'],
                        level='ERROR' if isinstance(t['result'],dict) and t['result'].get('error') else None))
                    messages += [{'role':'tool','tool_call_id':f'call-{n}','name':t['name'],'content':json.dumps(t['result'],ensure_ascii=False)}]
            generation('answer', messages, {'role':'assistant','content':case['answer']}, case['input_tokens'], 1800)
        events.append(observation_event(**common, obs_id=r.obs_id('output-guardrail'), parent_id=chain,
            name='output-guardrail', obs_type='guardrail', start=ts+timedelta(milliseconds=2400),
            end=ts+timedelta(milliseconds=2410), input=case['answer'], output={'delivered':case['answer'],'historical_fixture':True}))
        scores = [] if case['quality'] is None else [('policy-correctness',case['quality'],'NUMERIC')]
        scores += [(f'historical-policy-{key.lower()}',value,'CATEGORICAL') for key,value in sorted(case['policies'].items())]
        if case['policies']:
            scores += [('historical-compliance-status',case['expected_status'],'CATEGORICAL'),
                       ('historical-compliance-coverage',case['expected_coverage'],'NUMERIC')]
            if case['expected_headline'] is not None:
                scores.append(('historical-compliance',case['expected_headline'],'NUMERIC'))
        for name,value,kind in scores:
            events.append(score_event(score_id=r.score_id(name), trace_id=trace_id, observation_id=subject,
                name=name,value=value,data_type=kind,timestamp=ts,environment='synthetic-history',
                comment='Synthetic authoring fixture; API source, not managed EVAL or human annotation. conduct-v2; '+case['id']))
        prior += [{'role':'user','content':case['query']},{'role':'assistant','content':case['answer']}]
    return events
