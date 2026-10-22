"""Verify every mandatory historical turn against the successfully imported current run."""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

import requests

from langfuse_synth_core.read import ReadError
from langfuse_synth_core.target import TargetProfile

from .config import Config
from .receipt import RunReceipt
from .story import check_case, validate_anchors


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class VerifyReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)



def run_verify(
    cfg: Config, *, timeout_seconds: float = 60, poll_interval: float = 2,
    clock=time.monotonic, sleep=time.sleep, log=print,
) -> VerifyReport:
    """Read the last successful seed's records, polling visibility for at most 60 seconds.

    A zero timeout does one pass. The deadline bounds polling; an in-flight request uses
    core's own HTTP timeout. Inject a monotonic clock and sleep to test without waiting.
    """
    if (not math.isfinite(timeout_seconds) or timeout_seconds < 0
            or not math.isfinite(poll_interval) or poll_interval <= 0):
        raise ValueError("timeout_seconds must be finite and nonnegative; poll_interval positive")
    log("Reading exact current-run receipt", flush=True)
    report = VerifyReport()
    try:
        receipt = RunReceipt.load()
        validate_anchors(receipt.history_anchors, receipt.history_run_id)
        if (receipt.base_url != cfg.target.base_url
                or receipt.public_key != os.environ.get("LANGFUSE_PUBLIC_KEY", "")
                or receipt.seed != cfg.generation.seed
                or receipt.target_traces != cfg.generation.target_traces
                or receipt.as_of_date != str(cfg.generation.as_of_date or "")):
            raise ValueError("receipt target or generation configuration differs from invocation")
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        report.add("run_receipt", False, f"Missing or invalid receipt; run synth seed: {exc}")
    else:
        profile = TargetProfile.detect(cfg.target.base_url)
        reader = profile.reader(attempts=1)
        log(f"· verifying against {profile.label} ({profile.base_url})")
        deadline = clock() + timeout_seconds
        while True:
            try:
                report = VerifyReport()
                for case in receipt.history_anchors['cases']:
                    report.add(case['case_id'], *check_case(reader, case))
            except (ReadError, requests.RequestException) as exc:
                report = VerifyReport()
                report.add("target_read", False, f"Cannot read expected records: {exc}")
            if report.ok:
                break
            remaining = deadline - clock()
            if remaining <= 0:
                for check in report.checks:
                    if not check.ok:
                        check.detail += f" (visibility timeout after {timeout_seconds:g}s)"
                break
            log("Waiting for current-run records to become visible", flush=True)
            sleep(min(poll_interval, remaining))
    for check in report.checks:
        log(f"{'✓' if check.ok else '✗'} {check.name}: {check.detail}")
    return report
