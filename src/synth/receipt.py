"""Exact history receipt on the spool volume, isolated from live Companion state."""
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from langfuse_synth_core.anchors import AnchorsIO


@dataclass
class RunReceipt(AnchorsIO):
    FALLBACK_STATE_DIR: ClassVar[Path] = Path(__file__).resolve().parents[2] / '.synth_spool'
    base_url: str
    public_key: str
    seed: int
    target_traces: int
    as_of_date: str
    history_run_id: str
    history_anchors: dict
    history_days: int

    @classmethod
    def state_path(cls) -> str:
        return str(cls.state_dir() / '.synth_verify.json')
