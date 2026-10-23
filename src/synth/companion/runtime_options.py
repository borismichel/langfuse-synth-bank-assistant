"""Validated nonsecret options from the deployment's read-only state mount."""
import json
from pathlib import Path
from synth.receipt import RunReceipt


def enabled(name):
    allowed = {'apm_enabled', 'n8n_enabled'}
    if name not in allowed:
        raise ValueError('Unknown runtime option')
    path = Path(RunReceipt.state_dir()) / 'runtime-config.json'
    if not path.exists():
        return False
    if path.stat().st_size > 4096:
        raise ValueError('Invalid runtime configuration')
    config = json.loads(path.read_text())
    if (not isinstance(config, dict) or set(config) - allowed
            or any(type(value) is not bool for value in config.values())):
        raise ValueError('Invalid runtime configuration')
    return config.get(name, False)
