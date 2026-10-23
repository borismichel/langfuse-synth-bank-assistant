"""Temporary, opt-in diagnostics for the hosted authoring-stage timeout.

The authoring worker captures pytest output and discards it on timeout. Keep
only test timings and Python stacks in files outside that captured stream.
No environment, request payloads, exception values, or locals are recorded.
Remove this probe after the hosted timeout has a reproduced cause.
"""
from __future__ import annotations

import sys
from threading import Event, Thread
import json
import os
import platform
import time
from datetime import datetime, timezone

_progress = None
_stacks = None
_started = 0.0
_stack_stop = Event()
_stack_thread = None


def _record(event, **fields):
    if _progress is not None:
        _progress.write(json.dumps({
            "probe": "DEBUG-experiment-wrapper", "event": event,
            "utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - _started, 3),
            **fields,
        }) + "\n")
        _progress.flush()


def pytest_configure(config):
    global _progress, _stacks, _started, _stack_thread
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
    def snapshots():
        while not _stack_stop.wait(10):
            _stacks.write("Python stack snapshot\n")
            for identity, frame in sys._current_frames().items():
                _stacks.write(f"Thread {identity}\n")
                while frame is not None:
                    code = frame.f_code
                    _stacks.write(f"  {code.co_filename}:{frame.f_lineno} in {code.co_name}\n")
                    frame = frame.f_back
            _stacks.flush()
    _stack_thread = Thread(target=snapshots, daemon=True)
    _stack_thread.start()


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
    _stack_stop.set()
    _stack_thread.join()
    _stacks.close()
    _progress.close()
    _stacks = _progress = None


import pytest


@pytest.fixture(autouse=True)
def experiment_boundaries(request, monkeypatch):
    if _progress is None or request.node.path.name != "sdk_experiments_case.py":
        yield
        return
    from fastapi.testclient import TestClient
    from collections import defaultdict
    from threading import Event, Lock, Thread
    import httpx
    import requests

    counts = defaultdict(lambda: [0, 0.0])
    lock = Lock()
    stop = Event()
    cpu = time.process_time()
    phase = "setup"
    allowed = {"comparison-one", "selling", "retrieval-cost", "dispute-window",
               "spanish-language", "cap-denied", "cancelled", "edited-cohort"}

    def bucket(url):
        path = str(url).split("?", 1)[0]
        for name in ("messages", "operation-permit", "scores", "observations",
                     "experiment-items", "traces", "status", "dataset-items"):
            if path.endswith("/" + name):
                return name
        return "other"

    def add(url, elapsed):
        with lock:
            item = counts[bucket(url)]
            item[0] += 1
            item[1] += elapsed

    def summary():
        with lock:
            stats = {key: {"calls": value[0], "seconds": round(value[1], 3)}
                     for key, value in counts.items()}
        _record("http_summary", phase=phase, cpu_seconds=round(time.process_time()-cpu, 3),
                requests=stats)

    def sample():
        while not stop.wait(5):
            summary()

    def wrap_send(original):
        def send(self, outgoing, *args, **kwargs):
            started = time.monotonic()
            try:
                return original(self, outgoing, *args, **kwargs)
            finally:
                add(outgoing.url, time.monotonic()-started)
        return send

    async_original = httpx.AsyncClient.send
    async def async_send(self, outgoing, *args, **kwargs):
        started = time.monotonic()
        try:
            return await async_original(self, outgoing, *args, **kwargs)
        finally:
            add(outgoing.url, time.monotonic()-started)

    monkeypatch.setattr(httpx.Client, "send", wrap_send(httpx.Client.send))
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    monkeypatch.setattr(requests.Session, "send", wrap_send(requests.Session.send))
    from synth.companion.experiments import ExperimentControls
    original_persist = ExperimentControls.persist
    def persist(self):
        started = time.monotonic()
        try:
            return original_persist(self)
        finally:
            with lock:
                counts["receipt_persist"][0] += 1
                counts["receipt_persist"][1] += time.monotonic()-started
    monkeypatch.setattr(ExperimentControls, "persist", persist)
    original_request = TestClient.request
    statuses = {}
    def browser_request(self, method, url, *args, **kwargs):
        nonlocal phase
        action = str(url).rsplit("/", 1)[-1]
        if method.upper() == "POST":
            identity = kwargs.get("json", {}).get("request_id")
            if action == "start" and identity in allowed:
                phase = identity
            _record("control_start", phase=phase, action=action)
        started = time.monotonic()
        result = original_request(self, method, url, *args, **kwargs)
        if method.upper() == "POST":
            _record("control_end", phase=phase, action=action,
                    duration_seconds=round(time.monotonic()-started, 3), status=result.status_code)
        if action == "status" and result.status_code == 200:
            for run in result.json().get("runs", []):
                identity = run.get("request_id")
                status = run.get("status")
                if identity in allowed and statuses.get(identity) != status:
                    statuses[identity] = status
                    _record("comparison_status", phase=identity, status=status,
                            dispatches=run.get("dispatches"))
        return result
    monkeypatch.setattr(TestClient, "request", browser_request)

    def wrap_lifecycle(original, event):
        def wrapped(self, *args, **kwargs):
            _record(event + "_start", phase=phase)
            started = time.monotonic()
            try:
                return original(self, *args, **kwargs)
            finally:
                _record(event + "_end", phase=phase,
                        duration_seconds=round(time.monotonic()-started, 3))
        return wrapped
    monkeypatch.setattr(TestClient, "__enter__", wrap_lifecycle(TestClient.__enter__, "app_start"))
    monkeypatch.setattr(TestClient, "__exit__", wrap_lifecycle(TestClient.__exit__, "app_stop"))
    sampler = Thread(target=sample, daemon=True)
    sampler.start()
    try:
        yield
    finally:
        stop.set()
        sampler.join()
        summary()
