"""Validated banking generation values; core owns loading and canonical volume overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from langfuse_synth_core.config import load_config as _load_config
from langfuse_synth_core.derivation import identity_derivation
from langfuse_synth_core.timegen import parse_as_of_date


@dataclass
class Target:
    """The Langfuse instance a run points at (the library reads `base_url`).

    `host` is the committed default and `LANGFUSE_BASE_URL` overrides it — the retargeting
    rule of CONTRACT.md §"Retargeting". A kit that reads only its config file dials its own
    loopback wherever it is deployed (portal #187); `tests/test_retargeting.py` gates
    against exactly that.
    """

    host: str = "http://localhost:3000"
    project_hint: str = "demo"

    @property
    def base_url(self) -> str:
        # env wins so the same shipped config can target different instances
        return os.environ.get("LANGFUSE_BASE_URL", self.host).rstrip("/")


@dataclass
class Generation:
    seed: int = 42
    target_traces: int = 64
    history_days: int = 7
    # The operator's as-of date: the portal sends `--set generation.as_of_date=YYYY-MM-DD`
    # on every forward generate (portal #72), and the seeded window ends on that day. None
    # means "no tether set" — the CLI path and the no-tether portal path both omit the key —
    # and resolves to the wall clock at seed time (`timegen.resolve_run_date`). A future date
    # is by design (portal #229): never clamp, warn or reject it.
    as_of_date: date | None = None


@dataclass
class Runtime:
    apm_enabled: bool = False
    n8n_enabled: bool = False


@dataclass
class Config:
    target: Target
    generation: Generation
    runtime: Runtime = field(default_factory=Runtime)


def _model_factory(raw: dict) -> Config:
    """Validate a raw config dict into the kit `Config` (the library's `dict -> Config`)."""
    raw = raw or {}
    target = raw.get("target") or {}
    generation = raw.get("generation") or {}
    unknown = set(generation) - {'seed', 'target_traces', 'history_days', 'as_of_date'}
    if unknown:
        raise ValueError('Unknown generation fields: ' + ', '.join(sorted(unknown)))
    for key, default, minimum, maximum in [('target_traces', 64, 58, 256),
                                           ('history_days', 7, 1, 90), ('seed', 42, 0, 2147483647)]:
        value = generation.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'generation.{key} must be an integer from {minimum} to {maximum}')
    if generation.get('history_days', 7) not in (1, 2, 7, 30, 90):
        raise ValueError('generation.history_days must be 1, 2, 7, 30 or 90')
    enabled = (raw.get('runtime') or {}).get('apm_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('runtime.apm_enabled must be a boolean')
    n8n = (raw.get('runtime') or {}).get('n8n_enabled', False)
    if type(n8n) is not bool:
        raise ValueError('runtime.n8n_enabled must be a boolean')
    return Config(
        runtime=Runtime(apm_enabled=enabled, n8n_enabled=n8n),
        target=Target(
            host=str(target.get("host", "http://localhost:3000")),
            project_hint=str(target.get("project_hint", "demo")),
        ),
        generation=Generation(
            seed=int(generation.get("seed", 42)),
            target_traces=int(generation.get("target_traces", 64)),
            history_days=generation.get("history_days", 7),
            as_of_date=parse_as_of_date(generation.get("as_of_date")),
        ),
    )


def load_config(path: str | Path, overrides: list[str] | None = None) -> Config:
    """Read the YAML config, apply `--set dotted.key=value` overrides, and build `Config`."""
    return _load_config(path, _model_factory, overrides)


# A turn is one trace; background growth never changes the fixed 58 required turns.
DERIVATION_HOOK = identity_derivation
