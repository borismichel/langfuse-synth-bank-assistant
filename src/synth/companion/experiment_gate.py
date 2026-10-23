"""Versioned gate over complete, verified application measurements."""
import math

VERSION='northwind-application-gate-v1'
TOKEN_RATIO=1.25


def assess(expected, baseline, candidate, *, required=None):
    """Both arms need exact membership, mandatory scores and measured usage."""
    ids=set(expected)
    counts={'expected':len(ids),'scored':0,'missing':0,'errors':0,'failed':0}
    if not ids or not required or set(required)!=ids:return {**counts,'status':'incomplete','gate_version':VERSION}
    arms=[]
    for rows in (baseline,candidate):
        mapped={r['case_id']:r for r in rows}
        if len(mapped)!=len(rows) or set(mapped)-ids:
            return {**counts,'status':'runner_failed','errors':1,'gate_version':VERSION}
        arms.append(mapped)
    for case in expected:
        left,right=(a.get(case) for a in arms)
        if any(r and r.get('error') for r in (left,right)):
            counts['errors']+=1;continue
        def complete(r):
            return (r and r.get('verified') and r.get('checks') and
                set(r['checks'])==set(required[case]) and
                all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in r['checks'].values()) and
                isinstance(r.get('usage'),dict) and set(r['usage'])=={'input','output'} and
                all(type(v) is int and v>=0 for v in r['usage'].values()))
        if not complete(left) or not complete(right) or set(left['checks'])!=set(right['checks']):
            counts['missing']+=1;continue
        counts['scored']+=1
        if (any(v<1 for v in left['checks'].values()) or any(v<1 for v in right['checks'].values()) or
                sum(right['usage'].values())>max(1,sum(left['usage'].values()))*TOKEN_RATIO):
            counts['failed']+=1
    status=('runner_failed' if counts['errors'] else 'incomplete' if counts['missing'] else
            'failed' if counts['failed'] else 'passed')
    return {**counts,'status':status,'gate_version':VERSION,'max_token_ratio':TOKEN_RATIO,
        'cost_measure':'Actual application input plus output tokens; same primary model. Judge costs are separate.'}
