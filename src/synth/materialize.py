"""A frozen bank-dispute-policy retrieval regression, entirely model-free at seed time.

Each session compares the same question across three releases. The first complete
session is always present; extra volume repeats it with fresh IDs. This is authored
evidence, not output from a live model or evaluator.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from langfuse_synth_core.rng import Rng
from langfuse_synth_core.seed.events import (
    generation_event, observation_event, score_event, trace_event,
)

from .config import DERIVATION_HOOK

QUESTION = "I reported an unauthorised card payment 21 days after the statement date. Can you investigate?"
CURRENT_POLICY = "Synthetic Northwind policy: Report an unauthorised card payment within 60 days of the statement date for investigation; do not guarantee a refund."
RETIRED_POLICY = "Retired synthetic Northwind policy: Reports must be received within 14 days."
BEATS = (
    ("baseline", "bank-v1", CURRENT_POLICY, "Yes. Your report is within 60 days. We can investigate; a refund is not guaranteed.", 1.0),
    ("failure", "bank-v2", RETIRED_POLICY, "No. The 14 days reporting window has expired.", 0.0),
    ("recovery", "bank-v3", CURRENT_POLICY, "Yes. Your report is within 60 days. We can investigate; a refund is not guaranteed.", 1.0),
)


def build_events(
    target_traces: int, params: Mapping[str, Any], *, run_date: datetime,
) -> list[dict]:
    """Return exactly target_traces, retaining one complete three-beat example.

    Counts below three are unsupported; higher nonmultiples of three leave a partial
    background session after the complete examples. Increasing volume preserves the
    first session's IDs, contents and dates. All three phases fit inside the run window.
    """
    count = int(DERIVATION_HOOK(target_traces, params)["target_traces"])
    if count < 3:
        raise ValueError("regression-recovery needs at least 3 traces for all story beats")
    seed = int(params.get("seed", 42))
    rng = Rng(seed)
    events = []
    for i in range(count):
        case, phase = divmod(i, 3)
        beat, release, policy, answer, correctness = BEATS[phase]
        ids = [rng.sub("bank-dispute", case).trace_id(j) for j in range(3)]
        trace_id = ids[phase]
        r = rng.sub("bank-dispute", case, phase)
        # Baseline three days back, failure two, recovery one; case offset <= 1 hour.
        ts = run_date - timedelta(days=3 - phase, seconds=case % 3600)
        retrieval_ms = (120, 340, 150)[phase]
        generation_ms = (760, 820, 780)[phase]
        retrieved_at = ts + timedelta(milliseconds=retrieval_ms)
        finished_at = retrieved_at + timedelta(milliseconds=generation_ms)
        metadata = {
            "beat": beat, "release": release, "evidence": "seeded-fixture",
            "baseline_trace_id": ids[0], "previous_trace_id": ids[max(0, phase - 1)],
            "retrieval_filter": "none" if phase == 1 else "status=current",
            "policy_source": "KB-102", "upstream_commit": "513efb14fda3efeb61179285354fb9a63ebc36d1",
        }
        events.append(trace_event(
            trace_id=trace_id, timestamp=ts, name=f"bank-dispute-{beat}",
            user_id=f"customer-{case:04d}", session_id=f"bank-dispute-{seed}-case-{case:04d}",
            tags=["seeded", "bank-dispute-policy", beat], metadata=metadata,
            input=QUESTION, output=answer,
        ))
        events.append(observation_event(
            obs_id=r.obs_id("retrieve"), trace_id=trace_id, name="retrieve-bank-dispute-policy",
            obs_type="retriever", start=ts, end=retrieved_at, input=QUESTION,
            output={"document": policy}, metadata={"filter": metadata["retrieval_filter"]},
        ))
        events.append(generation_event(
            obs_id=r.obs_id("answer"), trace_id=trace_id, name="answer-bank-dispute-question",
            start=retrieved_at, end=finished_at, model="fixture-bank-model",
            input={"question": QUESTION, "context": policy}, output=answer,
            usage_details={"input": 96, "output": 17, "total": 113},
            cost_details={"input": 0.0, "output": 0.0, "total": 0.0},
            metadata={"release": release, "evidence": "seeded-fixture"},
        ))
        events.append(score_event(
            score_id=r.score_id("policy-correctness"), trace_id=trace_id,
            observation_id=r.obs_id("answer"), name="policy-correctness", value=correctness, data_type="NUMERIC", timestamp=ts,
            comment="Seeded fixture label: the current synthetic Northwind reporting window is 60 days; no refund is guaranteed.",
        ))
    return events
