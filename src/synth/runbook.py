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


def presenter_runbook():
    """Deliver option instructions inside the downloaded primary Markdown."""
    chapters = [Path('DEMO_SCRIPT.md')] + [Path('docs') / name for name in (
        'VOICE.md', 'N8N.md', 'APM.md', 'CODING_AGENT_WORKSHOP.md')]
    parts = [chapters[0].read_text()]
    for chapter in chapters[1:]:
        # Nest each complete chapter without changing its instructions or links.
        parts.append('\n'.join('#' + line if line.startswith('#') else line
                               for line in chapter.read_text().splitlines()))
    return '\n\n'.join(parts) + '\n'
