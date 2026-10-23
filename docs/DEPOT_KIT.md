# Northwind banking Depot package

This repository is a maintained fork of doneyli/clickhouse-llm-observability. The root `usecase.yaml` registers only the Northwind banking package. Upstream history and other demos remain available as source; the runtime image includes only banking and package dependencies.

Use Depot's **Deploy demo** flow against Langfuse Cloud or self-hosted Langfuse 4.59.0 or later. The deployment validates the target, displays the volume estimate, waits for the standard confirmation, sets up the initial production prompt without replacing an existing version and content-addressed history prompts, generates the complete frozen history, applies Depot's measured spool cap, imports once and verifies the exact run. History generation makes no model calls. The live assistant uses the provider and credentials selected in Depot; health checks validate connectivity and client construction without inference.

The complete implementation scope is [Depot spec #365](https://github.com/borismichel/langfuse-demo-depot/issues/365). See the delivered Presenter Runbook for implemented and pending capabilities. Foundation checks do not constitute full-spec qualification.

## Developer mode

Use Python 3.12.15, `uv sync --frozen --extra dev`, then `.venv/bin/synth-authoring check . --python .venv/bin/python --json`. The exact runtime, SDK and authoring versions are locked in `uv.lock`; runtime and authoring share the tested immutable core integration candidate recorded in provenance. The publishing workflow stays on the v5.0.0 commit. A coordinated stable core release remains required before full qualification.

Generate a credential-free example with isolated writable paths:

```sh
SYNTH_STATE_DIR=/tmp/bank-state SYNTH_OUT_DIR=/tmp/bank-output .venv/bin/synth generate-spool --config config/demo.yaml --set generation.as_of_date=2026-10-20
```

Target credentials come only from Depot's runtime environment. Do not use upstream workstation launchers, `.env` fallbacks, project database access or the full Compose stack for this package.

Import is append-only and cannot resume. A completed or failed import blocks both import and regeneration for that deployment. Use a fresh disposable project/deployment or an explicitly authorised reset. Stopping the package does not remove remote observations.

## Companion

The delivered banking interface retains the upstream bilingual LangGraph retrieval, guardrails and native MCP tools. Its Adapter binds the selected Langfuse target, native provider client and single custom tracing pipeline. A separate HttpOnly browser cookie authorises the conversation; only a distinct public session ID enters Langfuse. Customer state is private to a participant and deployment. Card blocks, disputes and callbacks require a displayed action followed by explicit confirmation; confirmed retries retain the original receipt. The app supervises one private loopback MCP child and reports child failures in readiness and logs. New conversation retains banking changes; Reset demo state restores only the owned selected customer, cancels pending work and rotates session/run identities. Feedback names the saved answer observation and reuses its score ID/timestamp on edits, with target readback before success. Browser state is validated after reload and invalidated after replacement. Optional voice supports recording and upload through the same owned conversation, with separate OpenAI speech roles and local playback. See [Voice](VOICE.md) for limits, qualification and recovery. Application experiment controls are described below.

For credential-free development, run `.venv/bin/python -m synth.companion.preview`. This separate process clears inherited credentials, blocks outgoing connections and serves labelled fixture answers on loopback. Preview readiness is always false; it cannot qualify a live deployment.

## Governance

The protected **Governance** page reads target connectivity and the exact 4.68
retention setting. It labels unproven native roles, audit access and protected-label
enforcement as unavailable, with documentation and native inspection links. See
[Governance qualification](GOVERNANCE.md) for capability states, project-key limits
and the authorized actual-target rehearsal record.

## Historical fixture contract

The portable corpus is `src/synth/history.json`, frozen from upstream revision `513efb14fda3efeb61179285354fb9a63ebc36d1` plus explicitly constructed scenario controls. It preserves four upstream customers, the complete knowledge corpus and 22 upstream conduct examples. Expected labels are author-supplied synthetic fixtures, not independent human review. Recipe generation makes no network/model calls and the golden records full output at the 58-turn mandatory floor.

`generation.target_traces` currently supports 58–256 (default 64). Extra volume appends background turns and cannot remove required scenarios or change their IDs/timestamps. The run identity includes the seed, resolved date and corpus hash. The canonical spool is target-independent; actual prompt versions are resolved during setup and rechecked before import. The bound import has the same measured trace/observation/score counts. No audio or other historical media assets are included.

`pending-run.json` describes unimported output. Only successful import writes `.synth_verify.json`: target identity, generation inputs, `history_run_id`, and `history_anchors` schema version 1 with all required case IDs, trace IDs, delivered-answer observation IDs, session IDs and timestamps, plus verification expectations. Companion history links may consume these IDs; they must not treat this receipt as live session authority or mutate it on Reset demo state. The generated runbook includes every case link. Live qualification must still verify the real target's prompt associations, API score provenance and complete history visibility.

## Application experiments and quality gate

Open **Application experiments** through the authenticated Depot deployment. This manage-only route replays the complete fixed English (16), Spanish (10), red-team (10), or dispute (10) cohort through the actual LangGraph retrieval and private MCP service. Each item and comparison arm gets independent banking state. A dispute confirmation is submitted only for the exact expected transaction; it is recorded as a second real turn. Native prompt-only playground experiments have a different scope.

**Prepare control and regression prompt versions** publishes missing, unlabelled copies of the shipped V5 control and V3 selling/English-only regression. It preserves `production` and reuses matching exact content. Enter exact baseline/candidate versions and an exact judge model available on the deployment’s selected provider. Use the same control version in both fields for a passing-control rehearsal. Compare the regression version on English and Spanish for conduct/language failures; use **30-day transaction lookback** on disputes and **up to 8 retrieved articles** on English for the original dispute/cost regressions. Actual model results may differ: the measured gate, not the scenario label, decides the outcome.

A comparison freezes dataset snapshot/item IDs/source relationships, prompt content/version, five prepared conduct rubrics, primary model/provider, candidate policy and the gate version. Both arms use the same primary model. The judges receive the exact delivered query, answer and marked authoritative evidence, never constructed expected labels. They are explicit local API-source evaluations, separate from managed live EVAL rules; experiment roots remain ineligible for those live rules. Individual judge generation usage, latency and verdicts remain visible in the comparison receipt and native traces.

The gate requires exact native experiment/assistant/score readback and complete mandatory results for both arms. It measures source/fact checks, language/register, unsolicited selling, five-policy conduct and actual dispute receipts. It rejects candidate application token consumption above 1.25 times its matched baseline. This is measured input-plus-output tokens on one fixed model, not a billing-price estimate; judge costs are shown separately. A failed baseline also blocks approval. Unknown/invalid conduct verdicts, absent usage or missing native records cannot pass. The displayed expected/scored/missing/error/failed counts refer to paired cases; each row retains its individual checks. A pass covers the selected cohort only; release promotion must require the complete required cohort set and revalidate the frozen identities.

Execution is sequential: at most 16 cases per arm, two arms, ten application dispatches and five judge calls per case, 480 total dispatches and a ten-minute scheduling deadline. Provider retries are disabled; application output is capped at 1,024 tokens per dispatch, judges at 500. Every model call makes a fresh generic Depot operation-spending check. `spending_check: {version: 1}` requires the host’s API, worker and internal Caddy bridge to enable that contract. Missing permission/transport/cap availability fails closed before dispatch. The hourly shared-provider cap is an estimate, not a reservation or hard billing ceiling. Remote managed evaluator queues have separate costs and may continue independently.

Repeated clicks with the same request identity return the existing comparison; changing its settings returns a conflict. One comparison runs at a time, with a maximum of eight per deployment. **Cancel pending work** prevents subsequent dispatches; a request already in flight may complete and remain billable. Stopping the Companion cancels and drains the runner before the MCP process and SDK shut down. Request identities and measured receipts use private, bounded container-local runtime storage; the imported historical spool remains read-only. A process restart within the same container marks interrupted work and never automatically replays it. Replacing the live container removes its local receipts; inspect existing native experiments before starting new work. **Refresh native evidence** only reads existing results, so ingestion lag is safe to retry. Inspect partial native results after a transport error, interruption or unknown publication outcome; use **New comparison** and acknowledge costs again for a deliberate repeat with the same settings. Stopping a deployment preserves its remote traces/scores; project cleanup remains an explicit host operation.

The reusable comparison state is `experiments-<target-and-instance-hash>.json` under `SYNTH_STATE_DIR`; it contains no provider or ingress credentials. Native experiment IDs/URLs and exact assistant observation IDs support later judge comparisons and promotion, without treating synthetic references as independent human review. Target qualification must still rehearse the delivered actions against the actual installed Langfuse version and provider. Model-free tests use the SDK 5.0.4 and the 4.68 public API contracts.
