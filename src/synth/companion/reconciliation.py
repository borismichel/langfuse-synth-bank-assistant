"""Model-free reduction of exact managed results and owned derived scores."""
from datetime import datetime
from northwind.compliance import POLICIES, aggregate


def newest(matches, policy):
    """Later native updates supersede earlier ones; equal-time conflicts are explicit."""
    def stamp(row):
        value=row.get('updatedAt') or row['timestamp']
        return value if isinstance(value,datetime) else datetime.fromisoformat(value.replace('Z','+00:00'))
    latest=max(map(stamp,matches))
    tied=[r for r in matches if stamp(r)==latest]
    labels={str(r.get('value')) for r in tied}
    ambiguous=len(labels)>1
    # A simultaneously known failure must not disappear behind an uncertain label.
    ordered=sorted(tied,key=lambda r:(r['id'],str(r.get('value')),str(r.get('comment'))))
    row=next((r for r in ordered if r.get('value')=='fail'),ordered[0])
    invalid=row.get('dataType')!='CATEGORICAL' or row.get('value') not in policy.categories
    value='unknown' if invalid or (ambiguous and row.get('value')!='fail') else row['value']
    return dict(state='invalid' if invalid else 'ambiguous' if ambiguous else 'scored',value=value,
        score_id=row['id'],reasoning=row.get('comment'),updated_at=stamp(row).isoformat(),
        source_ids=sorted({r['id'] for r in tied}))


def summarize(results, blocked):
    counts=dict(eligible=len(results),scored=0,unscored=0,blocked=blocked,pending=0,error=0,
                fully_scored=0,scored_policy_results=0,possible_policy_results=len(results)*5)
    for row in results:
        policies=row['policies'];present=[p for p in policies if p.get('score_id')]
        result=aggregate({p['policy']:p['value'] for p in policies})
        row['headline']=dict(status=result.status if present else 'unscored',score=result.score,coverage=result.coverage)
        counts['scored']+=bool(present);counts['unscored']+=not present
        counts['pending']+=any(p['state'] in ('pending','not_configured') for p in policies)
        counts['error']+=any(p.get('paused') or p['state'] in ('invalid','ambiguous','paused') or p['value']=='unknown' for p in policies)
        counts['fully_scored']+=len(present)==len(POLICIES)
        counts['scored_policy_results']+=len(present)
    return counts


class Reconciler:
    """Bounded read/plan/write/readback; provenance is checked before any deletion."""
    def __init__(self, judges):
        self.judges=judges
        self.state={'status':'waiting','window_hours':24,'interval_seconds':30,'error':None}
        self.halted=False;self.hold_path=None

    def snapshot(self):return dict(self.state)

    def run(self, *, retry=False):
        from langfuse.api.scores.types.create_score_request import CreateScoreRequest
        from northwind.compliance import OVERALL_NAME,STATUS_NAME,COVERAGE_NAME,RUBRIC_VERSION
        from synth.assets import fingerprint
        from .judges import plain
        j=self.judges
        self.state.update(status='reading',error=None)
        j.refresh()  # Complete candidate and EVAL scans before planning any mutation.
        if not j.rule:
            self.state.update(status='not_configured',checked_at=j.checked_at)
            return
        if retry:
            self.halted=False
            if self.hold_path:self.hold_path.unlink(missing_ok=True)
        desired={};remove=set();ownership={}
        for subject,result in zip(j.evidence['eligible'],j.results,strict=True):
            if not isinstance(subject['live_run_id'],str) or not subject['live_run_id']:
                raise ValueError('Live subject has no presentation run identity; no derived scores written')
            owner=dict(origin='northwind-managed-policy-reducer-v1',project_id=j.identity['project_id'],
                live_instance_id=j.instance,live_run_id=subject['live_run_id'],rubric=RUBRIC_VERSION,
                trace_id=subject['trace_id'],observation_id=subject['observation_id'],
                evaluation_rule_id=j.rule['id'],evaluator_versions={p:r['version_id'] for p,r in sorted(j.receipts.items())})
            version=fingerprint(result['policies'])
            for name,kind,value in ((STATUS_NAME,'CATEGORICAL',result['headline']['status']),
                    (COVERAGE_NAME,'NUMERIC',result['headline']['coverage']),
                    (OVERALL_NAME,'NUMERIC',result['headline']['score'])):
                sid='nw-reduced-'+fingerprint([owner,name])
                ownership[sid]=(owner,subject,name,kind)
                if value is None:remove.add(sid);continue
                desired[sid]=CreateScoreRequest(id=sid,timestamp=subject['timestamp'],name=name,
                    trace_id=subject['trace_id'],observation_id=subject['observation_id'],
                    source='API',data_type=kind,value=value,environment=j.environment,
                    metadata={**owner,'input_fingerprint':version},comment='Derived from managed policy verdicts; no additional model call.')
        def read():
            found={};ids=list(ownership)
            for offset in range(0,len(ids),75):
                for row in j.pages(j.api.scores.get_many,id=','.join(ids[offset:offset+75]),fields='subject,details'):
                    d=plain(row)
                    if d['id'] not in ownership:raise ValueError('Target score ID filter mismatch; no derived scores changed')
                    owner,subject,name,kind=ownership[d['id']]
                    meta=d.get('metadata') or {}
                    if (d.get('source')!='API' or d.get('name')!=name or d.get('dataType')!=kind or
                        d.get('environment')!=j.environment or d.get('subject')!={'kind':'observation','id':subject['observation_id'],'traceId':subject['trace_id']} or
                        any(meta.get(k)!=v for k,v in owner.items()) or d.get('timestamp')!=subject['timestamp']):
                        raise ValueError('Derived score ownership conflict; existing score preserved')
                    if d['id'] in found:raise ValueError('Duplicate derived score identity; inspect native scores')
                    found[d['id']]=d
            return found
        def matches(row,wanted):
            return row.get('value')==wanted.value and row.get('metadata')==wanted.metadata
        existing=read()
        complete=all(sid in existing and matches(existing[sid],row) for sid,row in desired.items()) and not (remove & existing.keys())
        if self.halted and not complete:
            self.state.update(status='action_required',error='Native readback has not confirmed the previous write; inspect native scores, then Retry reconciliation.',checked_at=j.checked_at)
            return
        if self.halted and complete:
            self.halted=False
            if self.hold_path:self.hold_path.unlink(missing_ok=True)
        # A partial native group after a lost worker filesystem is still unresolved.
        # Never infer a missing write from only part of its asynchronous readback.
        groups={}
        for sid,(owner,_,_,_) in ownership.items():
            groups.setdefault((owner['trace_id'],owner['observation_id']),[]).append(sid)
        if not retry and any(any(sid in existing and sid in desired and existing[sid].get('metadata')==desired[sid].metadata for sid in ids) and
                any(sid in desired and sid not in existing for sid in ids) for ids in groups.values()):
            self.state.update(status='pending',error='A derived score group is partially visible; waiting for native readback. Inspect before Retry reconciliation.',checked_at=j.checked_at)
            return
        # Deleting by ID is safe only after the complete ownership read passed.
        for sid in sorted(remove & existing.keys()):
            j.api.scores.delete(sid,request_options=j.request)
        changes=[row for sid,row in desired.items() if sid not in existing or not matches(existing[sid],row)]
        for offset in range(0,len(changes),100):
            try:
                if self.hold_path:self.hold_path.write_text('Native score write requires readback before retry.\n')
                response=j.api.scores.create(request=changes[offset:offset+100],request_options=j.request)
                if getattr(response,'rejected',0):raise ValueError('Derived score batch partially rejected; inspect native scores before Retry reconciliation')
                if self.hold_path:self.hold_path.unlink(missing_ok=True)
            except Exception:
                self.halted=True
                raise
        observed=read()
        pending=sum(sid not in observed or not matches(observed[sid],row) for sid,row in desired.items())+len(remove & observed.keys())
        self.state.update(status='pending' if pending else 'confirmed',checked_at=j.checked_at,
            eligible=len(j.results),pending_scores=pending,written=len(changes),retracted=len(remove & existing.keys()))
