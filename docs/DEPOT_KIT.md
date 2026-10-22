# Northwind banking Depot package

This repository is a maintained fork of doneyli/clickhouse-llm-observability. The root `usecase.yaml` registers only the Northwind banking package. Upstream history and other demos remain available as source; the runtime image includes only banking and package dependencies.

Use Depot's **Deploy demo** flow against Langfuse Cloud or self-hosted Langfuse 4.59.0 or later. The deployment validates the target, displays the volume estimate, waits for the standard confirmation, sets up the initial production prompt without replacing an existing version, generates frozen history, applies Depot's measured spool cap, imports once and verifies the exact run. History generation makes no model calls. The live assistant uses the provider and credentials selected in Depot; health checks validate connectivity and client construction without inference.

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

The delivered banking interface retains the upstream bilingual LangGraph retrieval, guardrails and native MCP tools. Its Adapter binds the selected Langfuse target, native provider client and single custom tracing pipeline. A separate HttpOnly browser cookie authorises the conversation; only a distinct public session ID enters Langfuse. Customer state is private to a participant and deployment. Card blocks, disputes and callbacks require a displayed action followed by explicit confirmation; confirmed retries retain the original receipt. The app supervises one private loopback MCP child and reports child failures in readiness and logs. New conversation retains banking changes; Reset demo state restores only the owned selected customer, cancels pending work and rotates session/run identities. Feedback names the saved answer observation and reuses its score ID/timestamp on edits, with target readback before success. Browser state is validated after reload and invalidated after replacement. Voice and presenter experiments remain unavailable until their implementation tickets are integrated.

For credential-free development, run `.venv/bin/python -m synth.companion.preview`. This separate process clears inherited credentials, blocks outgoing connections and serves labelled fixture answers on loopback. Preview readiness is always false; it cannot qualify a live deployment.
