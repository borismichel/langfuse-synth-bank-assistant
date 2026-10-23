"""Measure declared history bounds through delivered commands in the runtime image."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from langfuse_synth_core.seed.count import count_spool

assert os.getuid() == 10001
results = []
baseline_cases = None
for volume in (58, 64, 256):
    with tempfile.TemporaryDirectory(prefix='history-volume-') as temporary:
        root = Path(temporary)
        env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'LANG')}
        env.update(SYNTH_STATE_DIR=str(root / 'state'), SYNTH_OUT_DIR=str(root / 'out'))
        options = ['--config', 'config/demo.yaml', '--set', f'generation.target_traces={volume}',
                   '--set', 'generation.as_of_date=2026-10-23']
        started = time.monotonic()
        plan = subprocess.run(['synth', 'plan', *options], env=env, capture_output=True, text=True, timeout=30)
        assert plan.returncode == 0, plan.stdout + plan.stderr
        expected = json.loads(next(line.removeprefix('PLAN_COUNTS=') for line in plan.stdout.splitlines()
                                   if line.startswith('PLAN_COUNTS=')))
        generated = subprocess.run(['synth', 'generate-spool', *options], env=env,
                                   capture_output=True, text=True, timeout=30)
        assert generated.returncode == 0, generated.stdout + generated.stderr
        spool = root / 'state/events.ndjson'
        measured = count_spool(spool)
        assert expected == {'version': 1, **{key: measured[key] for key in ('traces', 'observations', 'scores')}}
        assert measured['traces'] == volume
        receipt = json.loads((root / 'state/pending-run.json').read_text())
        cases = receipt['receipt']['history_anchors']['cases']
        assert len(cases) == 58
        if baseline_cases is None:
            baseline_cases = cases
        assert cases == baseline_cases, 'Required history identities changed with volume'
        assert not (root / 'state/.synth_verify.json').exists(), 'Generation must not claim target verification'
        results.append({'volume': volume, 'counts': measured, 'seconds': round(time.monotonic() - started, 3),
                        'spool_bytes': spool.stat().st_size, 'spool_sha256': hashlib.sha256(spool.read_bytes()).hexdigest(),
                        'required_cases': len(cases), 'run_date': receipt['run_date']})
print(json.dumps({'uid': os.getuid(), 'volumes': results,
                  'scope': 'Model-free plan and generated spool only; no import or target verification.'}))
