"""Dated evidence from exact verified subjects, never project-wide score totals."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import quote
from northwind.compliance import aggregate
from .assets import save, state_path
from .materialize import fixture


def run_window(history):
    """Absolute native filter from the hash-checked imported canonical spool."""
    pending = json.loads(state_path('pending-run.json').read_text())
    raw = state_path('events.ndjson').read_bytes()
    if (hashlib.sha256(raw).hexdigest() != pending['spool_sha256'] or
            pending['receipt']['history_run_id'] != history.history_run_id):
        raise ValueError('Current-run window cannot be established from imported spool')
    roots = [row for row in map(json.loads, raw.splitlines())
             if row.get('spanId') and not row.get('parentSpanId')]
    if len(roots) != history.target_traces:
        raise ValueError('Current-run spool root count differs from successful import')
    times = [int(row['startTimeUnixNano']) for row in roots]
    first, last = min(times), max(times)
    # Native filters can use strict bounds; one millisecond includes both endpoints.
    token = f'{first // 1_000_000 - 1}-{last // 1_000_000 + 1}'
    return dict(first=datetime.fromtimestamp(first / 1e9, timezone.utc).isoformat(),
                last=datetime.fromtimestamp(last / 1e9, timezone.utc).isoformat(),
                trace_count=len(roots), date_range=token)


def save_history_evidence(history, identity, assets, reader):
    cases = {c['id']: c for c in fixture()['cases']}
    counts = dict(total=0, eligible=0, blocked=0, scored=0, unscored=0, pending=0, unknown=0, invalid=0, errors=0)
    quality = dict(eligible=0, scored=0, passed=0, failed=0)
    rows = []
    project_url = history.base_url + '/project/' + quote(identity['project_id'], safe='')
    for anchor in history.history_anchors['cases']:
        case = cases[anchor['case_id']]
        expected_ids = {s['id'] for s in anchor['scores']}
        scores = [s for s in reader.scores(trace_id=anchor['trace_id'], limit_pages=1)
                  if s.id in expected_ids and s.source == 'API' and s.observation_id == anchor['observation_id']]
        if {s.id for s in scores} != expected_ids:
            raise ValueError('Current-run scores changed during evidence snapshot')
        verdicts = {s.name.removeprefix('historical-policy-').upper(): s.value for s in scores
                    if s.name.startswith('historical-policy-')}
        counts['total'] += 1
        if case['blocked']:
            counts['blocked'] += 1
            status = 'blocked'
        else:
            counts['eligible'] += 1
            counts['scored' if verdicts else 'unscored'] += 1
            counts['pending'] += len(verdicts) < 5
            counts['unknown'] += 'unknown' in verdicts.values()
            counts['invalid'] += any(v not in {'pass','fail','unknown','minor'} or
                                     (v == 'minor' and k != 'P3') for k,v in verdicts.items())
            status = aggregate(verdicts).status or 'unscored'
            if case['quality'] is not None:
                quality['eligible'] += 1
                score = next((s for s in scores if s.name == 'policy-correctness'), None)
                if score:
                    quality['scored'] += 1
                    quality['passed' if score.value == 1 else 'failed'] += 1
        rows.append(dict(case_id=anchor['case_id'], trace_id=anchor['trace_id'],
            observation_id=anchor['observation_id'], session_id=anchor['session_id'],
            timestamp=anchor['timestamp'], language=case['language'], status=status,
            url=project_url + '/traces/' + quote(anchor['trace_id'], safe='')))
    dates = run_window(history)
    snapshot = dict(schema_version=1, base_url=history.base_url, public_key=history.public_key,
        corpus_hash=assets['corpus_hash'], history_run_id=history.history_run_id, verified=True, verified_at=datetime.now(timezone.utc).isoformat(),
        status='Verified historical fixture snapshot', coverage=counts, quality=quality, cases=rows,
        datasets=[dict(cohort=key, **ds) for key,ds in assets['datasets'].items()],
        prompts=[dict(key=key, **p) for key,p in identity['prompts'].items()],
        scope='58 mandatory historical turns; background traffic and live evaluations excluded',
        provenance='Constructed API fixtures; not managed EVAL results or independent human reviews',
        coverage_note='Scored means at least one policy verdict. Pending means a missing historical verdict, not a queued job. Unknown and invalid are overlapping subsets. Errors count failed verification reads; no live evaluator error count is inferred.',
        dates=dates, native_views=dict(traces=project_url + '/traces?dateRange=' + dates['date_range']))
    save('history-evidence.json', snapshot)
    from .runbook import read_runbook, write_runbook
    text = read_runbook().split('\n\n## Verified history view\n', 1)[0]
    write_runbook(text + '\n\n## Verified history view\n\n' +
        f"[Native traces for the full imported time window]({snapshot['native_views']['traces']}) — {dates['trace_count']} imported roots from {dates['first']} to {dates['last']}. The time filter can include unrelated records in that interval; use the exact case links above for mandatory-run subjects.\n")
    return snapshot
