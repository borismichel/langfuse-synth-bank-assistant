"""Temporary, opt-in diagnostics for the hosted authoring-stage timeout.

The authoring worker captures pytest output and discards it on timeout. Keep
only test timings and Python stacks in files outside that captured stream.
No environment, request payloads, exception values, or locals are recorded.
Remove this probe after the hosted timeout has a reproduced cause.
"""
from __future__ import annotations

import faulthandler
import json
import os
import platform
import time
from datetime import datetime, timezone

_progress = None
_stacks = None
_started = 0.0


def _record(event, **fields):
    if _progress is not None:
        _progress.write(json.dumps({
            "probe": "DEBUG-banking-ci-372", "event": event,
            "utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - _started, 3),
            **fields,
        }) + "\n")
        _progress.flush()


def pytest_configure(config):
    global _progress, _stacks, _started
    directory = config.rootpath / ".ci-diagnostics"
    # A file opt-in survives the authoring worker's deliberate env allowlist.
    if not (directory / "enabled").is_file():
        return
    _started = time.monotonic()
    # The judge contract launches a second pytest process in this checkout.
    # Separate files prevent it from truncating the parent worker's evidence.
    _progress = (directory / f"progress-{os.getpid()}.jsonl").open("w", buffering=1)
    _stacks = (directory / f"stacks-{os.getpid()}.txt").open("w", buffering=1)
    _record("configured", python=platform.python_version(),
            system=platform.system(), architecture=platform.machine())
    faulthandler.dump_traceback_later(30, repeat=True, file=_stacks)


def pytest_collection_finish(session):
    _record("collected", tests=len(session.items))


def pytest_runtest_logstart(nodeid, location):
    _record("test_start", nodeid=nodeid)


def pytest_runtest_logreport(report):
    _record("test_phase", nodeid=report.nodeid, phase=report.when,
            outcome=report.outcome, duration_seconds=round(report.duration, 3))


def pytest_sessionfinish(session, exitstatus):
    _record("session_finish", exit_status=int(exitstatus))


def pytest_unconfigure(config):
    global _progress, _stacks
    if _progress is None:
        return
    _record("unconfigure")
    faulthandler.cancel_dump_traceback_later()
    _stacks.close()
    _progress.close()
    _stacks = _progress = None
