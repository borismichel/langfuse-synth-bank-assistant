"""Manifest command behavior over core's transport and read interfaces."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse

from packaging.version import Version, InvalidVersion
from langfuse_synth_core import lfread
from langfuse_synth_core.seed.ingest import Ingestor, assert_demo_project
from langfuse_synth_core.target import TargetProfile

from .receipt import RunReceipt


def run_probe(cfg) -> int:
    print('Checking target compatibility and project access', flush=True)
    if not all(os.environ.get(key) for key in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY')):
        raise ValueError('Depot project credentials are required; workstation files are never read.')
    host = urlparse(cfg.target.base_url).hostname
    if host not in {'cloud.langfuse.com', 'us.cloud.langfuse.com', 'jp.cloud.langfuse.com'}:
        health = lfread.get_json(cfg.target.base_url, '/api/public/health')
        try:
            version = Version(str(health['version']).removeprefix('v'))
        except (KeyError, InvalidVersion, TypeError) as exc:
            raise ValueError('Cannot establish target version; self-hosted Langfuse >=4.59.0 is required.') from exc
        if version < Version('4.59.0'):
            raise ValueError('Unsupported target: self-hosted Langfuse >=4.59.0 is required.')
    assert_demo_project(cfg.target.base_url, cfg.target.project_hint)
    TargetProfile.detect(cfg.target.base_url).reader(attempts=1).ping()
    print('Target read APIs and project access verified; no target data changed', flush=True)
    return 0


def run_plan(cfg) -> int:
    from langfuse_synth_core.timegen import resolve_run_date
    from tempfile import TemporaryDirectory
    from langfuse_synth_core.seed.count import count_spool
    from .materialize import build_events
    # Use the same materialization and core metering as the cap gate, in isolated state.
    with TemporaryDirectory(prefix='bank-plan-') as tmp:
        spool = Path(tmp) / 'events.ndjson'
        ingestor = Ingestor.from_env(cfg.target.base_url, dry_run=True, spool_path=spool)
        ingestor.open_spool()
        ingestor.extend(build_events(cfg.generation.target_traces, {'seed': cfg.generation.seed},
                                    run_date=resolve_run_date(cfg.generation.as_of_date)))
        ingestor.close_spool()
        measured = count_spool(spool)
    print('PLAN_COUNTS=' + json.dumps({'version': 1, **{key: measured[key] for key in
        ('traces', 'observations', 'scores')}}, separators=(',', ':')), flush=True)
    print('Seed model calls: 0. Historical answers, labels, token usage and costs are synthetic fixtures. Langfuse ingestion is billable according to the target plan.', flush=True)
    return 0


def run_import(cfg, *, spool_path=None, output_dir=None) -> int:
    from .seed import spool_file
    print('Checking generated run before append-only import', flush=True)
    spool = Path(spool_path) if spool_path else spool_file()
    pending = json.loads((spool.parent / 'pending-run.json').read_text())
    receipt = RunReceipt(**pending['receipt'])
    if (receipt.base_url != cfg.target.base_url
            or (receipt.public_key and receipt.public_key != os.environ.get('LANGFUSE_PUBLIC_KEY', ''))):
        raise ValueError('Target or configuration differs from generated run; no import performed.')
    if hashlib.sha256(spool.read_bytes()).hexdigest() != pending['spool_sha256']:
        raise ValueError('Generated spool changed; no import performed.')
    if Path(str(spool) + '.imported').exists():
        raise ValueError('Import already attempted. Use a fresh target/deployment or authorised project reset before another import.')
    run_probe(cfg)
    project_id, _ = assert_demo_project(cfg.target.base_url, cfg.target.project_hint)
    Path(RunReceipt.state_path()).unlink(missing_ok=True)
    from datetime import datetime
    from langfuse_synth_core.seed.count import count_spool
    from .history_prompts import read_bindings
    from .materialize import build_events
    from .story import history_anchors
    bindings = read_bindings(cfg)
    # Re-materialise through core with the recorded inputs and resolved prompt versions.
    # The measured canonical spool stays immutable and target-independent.
    bound_spool = spool.with_name('events.bound.ndjson')
    events = build_events(receipt.target_traces, {'seed': receipt.seed, 'prompt_bindings': bindings},
                          run_date=datetime.fromisoformat(pending['run_date']))
    bound = Ingestor.from_env(cfg.target.base_url, dry_run=True, spool_path=bound_spool)
    bound.open_spool()
    bound.extend(events)
    bound.close_spool()
    if count_spool(bound_spool) != count_spool(spool):
        raise ValueError('Prompt resolution changed measured record counts; no import performed')
    receipt.history_anchors = history_anchors(events)
    # Protect the canonical spool too: even a failed first network write is append-only.
    Path(str(spool) + '.imported').write_text('Bound import attempted; use a fresh target/deployment.\n')
    ingestor = Ingestor.from_env(cfg.target.base_url, spool_path=bound_spool)
    sent = ingestor.import_spool(path=bound_spool, confirm_cleared=False,
                                 log=lambda line: print(line, flush=True))
    receipt.public_key = os.environ['LANGFUSE_PUBLIC_KEY']
    receipt.save()
    out = Path(output_dir or os.environ.get('SYNTH_OUT_DIR', '/app/out'))
    out.mkdir(parents=True, exist_ok=True)
    project_url = cfg.target.base_url + '/project/' + quote(project_id, safe='')
    links = '\n'.join(f'- [{e["case_id"]}]({project_url}/traces/{e["trace_id"]}) — session `{e["session_id"]}`'
                      for e in receipt.history_anchors['cases'])
    (out / 'DEMO_SCRIPT.md').write_text(Path('DEMO_SCRIPT.md').read_text() +
        f'\n\n## This deployment\n\nTarget: {project_url}\n\nHistory run: {receipt.history_run_id} — {pending["run_date"]}\n\n{links}\n\nVerification runs next; import alone does not prove visibility.\n')
    print(f'Imported {sent} records; verification receipt and target runbook saved', flush=True)
    return 0
