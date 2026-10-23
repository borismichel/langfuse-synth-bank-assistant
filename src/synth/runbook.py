"""Carry the generated runbook through Depot's separate job artifact directories."""
import os
from pathlib import Path
from .assets import state_path


def read_runbook():
    return state_path('deployment-runbook.md').read_text()


def write_runbook(text, *, output_dir=None):
    # State persists between jobs; output is collected afresh for each job.
    saved=state_path('deployment-runbook.md')
    saved.parent.mkdir(parents=True,exist_ok=True)
    temporary=saved.with_suffix('.tmp')
    temporary.write_text(text)
    temporary.replace(saved)
    output=Path(output_dir or os.environ.get('SYNTH_OUT_DIR','/app/out'))
    output.mkdir(parents=True,exist_ok=True)
    (output/'DEMO_SCRIPT.md').write_text(text)
