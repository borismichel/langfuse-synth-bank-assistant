"""Model-free generation. Import is a separate, append-only Depot stage."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from langfuse_synth_core.seed.ingest import Ingestor
from langfuse_synth_core.timegen import resolve_run_date

from .config import Config
from .materialize import build_events, run_identity
from .receipt import RunReceipt
from .story import history_anchors


def spool_file() -> Path:
    return Path(RunReceipt.state_dir()) / 'events.ndjson'


def run_seed(cfg: Config, *, dry_run=False, do_import=True, spool_path=None,
             output_dir=None, runbook_source='DEMO_SCRIPT.md', log=print) -> Path:
    from .pipeline import run_import
    log('Generating frozen banking history', flush=True)
    spool = Path(spool_path) if spool_path else spool_file()
    # Generation cannot erase core's replay protection after a partial or completed import.
    if Path(str(spool) + '.imported').exists():
        raise RuntimeError('This deployment has attempted import. Use a fresh target and deployment, or an authorised reset; do not regenerate and retry.')
    Path(RunReceipt.state_path()).unlink(missing_ok=True)
    run_date = resolve_run_date(cfg.generation.as_of_date)
    events = build_events(cfg.generation.target_traces, {'seed': cfg.generation.seed}, run_date=run_date)
    ingestor = Ingestor.from_env(cfg.target.base_url, dry_run=True, spool_path=spool)
    ingestor.open_spool()
    ingestor.extend(events)
    ingestor.close_spool()
    pending = dict(base_url=cfg.target.base_url, public_key=os.environ.get('LANGFUSE_PUBLIC_KEY', ''),
                   seed=cfg.generation.seed, target_traces=cfg.generation.target_traces,
                   as_of_date=str(cfg.generation.as_of_date or ''),
                   history_run_id=run_identity(cfg.generation.seed, run_date),
                   history_anchors=history_anchors(events))
    (spool.parent / 'pending-run.json').write_text(json.dumps({
        'receipt': pending, 'spool_sha256': hashlib.sha256(spool.read_bytes()).hexdigest(),
        'run_date': run_date.isoformat(),
    }, sort_keys=True))
    artifact_dir = Path(output_dir or os.environ.get('SYNTH_OUT_DIR', '/app/out'))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / 'DEMO_SCRIPT.md').write_text(Path(runbook_source).read_text() +
        '\n\nGeneration completed. Target links are added only after a successful import.\n')
    log(f'Generated {cfg.generation.target_traces} traces; spool ready for measured volume gate', flush=True)
    if do_import and not dry_run:
        run_import(cfg, spool_path=spool, output_dir=artifact_dir)
    return spool
