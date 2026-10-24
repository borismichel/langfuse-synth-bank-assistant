# Banking candidate provenance

- Fork: https://github.com/borismichel/langfuse-synth-bank-assistant
- Upstream parent: https://github.com/doneyli/clickhouse-llm-observability
- Inspected upstream commit: `513efb14fda3efeb61179285354fb9a63ebc36d1`.
- Fork identity and unused destination checked by the integration coordinator before GitHub fork creation; upstream history is retained. No existing remote was repointed.
- Upstream Apache-2.0 licence remains in `LICENSE`; banking source and notices are preserved.
- Root package slug: `northwind-bank-assistant`; GHCR release identity: `ghcr.io/borismichel/langfuse-synth-bank-assistant`.
- Current package version: `0.8.0` Beta (`pyproject.toml`); planned GitHub release status: prerelease.
- Runtime, authoring and reusable publishing workflow: stable core `v5.1.1`, resolved by `uv.lock` to commit `f4fb77e8bcd312947621621131c7b6e4f0b4a6e5`. The patch adds compatible OTLP JSON acknowledgement handling to the existing core capabilities. These include the split-generation Manifest contract, native Companion factories, protected live-route transport gate, validated public origin, bounded fixed-target native OTLP forwarding, optional provider roles and fresh per-operation spending authorization.
- Python 3.12.15 and Langfuse SDK 5.0.4; full dependency resolution in `uv.lock`.

This is the authoritative narrative release record. Installation instructions refer
here rather than duplicating mutable versions. The executable identities remain
`pyproject.toml`, `uv.lock` and `.github/workflows/publish.yml`.

## Workflow audit

All four inherited workflows are archived in `.github/upstream-workflows`, outside the executable workflows directory. Claims and Langfuse prompt gates refer to unrelated demos or runtime Langfuse/provider secrets; the Northwind gate also requires runtime secrets and targets a workstation-style workflow. The inherited integrity job assumes the upstream monorepo deployment. None is enabled for this fork.

Only package CI and the pinned core release workflow are active. CI runs offline package checks and an isolated image build on GitHub-hosted runners. Semver tag publication builds native AMD64/ARM64 images, pushes GHCR digests and signs the resulting immutable index using GitHub OIDC/cosign. No runtime Langfuse or model-provider secret is supplied to GitHub.

## Product sources

Documentation inspected 22 October 2026:

- https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5
- https://langfuse.com/docs/observability/sdk/advanced-features
- https://langfuse.com/integrations/frameworks/langchain
- https://langfuse.com/llms.txt

Maintained Langfuse skill entrypoint SHA256: `e8908ea1c924374ba7bc5e82b220709d107db6a3450a0c4d72228a91a2a7534a`.

## Current candidate scope

Package `0.8.0` Beta integrates the implemented banking scope: participant sessions,
confirmation/reset/feedback, verified frozen history, native datasets and prompts,
managed policy judges, review/calibration, matched judge and application/provider
comparisons, prompt promotion/rollback, optional voice/n8n/APM, governance
inspection and the delivered runbook. The authored interfaces use the documented
Langfuse website design adaptation. See [interface design](INTERFACE_DESIGN.md).

The optional n8n runtime and n8n 2.42.4 dependencies are pinned separately in the
Dockerfile and npm lockfile. See [workflow operation](N8N.md) for its limits and
qualification requirements. Implemented scope is not a claim of full-spec
catalogue readiness.

## Retained intermediate history

The `0.7.0` release attempt did not publish an image. The publish workflow
failed conduct native-evidence readback because a source observation identifier
was masked. A separate CI run on the same source passed the offline package
checks, then failed the complaint-workflow image check because its test fixture
omitted the required operation permit. These are distinct hosted failures, not a
qualified image.

The `0.7.1` release attempt also did not publish an image: its hosted checks
exposed numeric SDK observation identifiers being masked in application output.
The `0.7.2` release added explicit owned evidence schemas and deterministic
numeric-ID regressions for application, provider comparison, calibration,
complaint and voice linkage while retaining customer-data masking. Both failed
release tags remain immutable.

Release `0.7.2`, source `fe8c3946fec91616b3b06abb4c1813e6b82082dc`, published
the signed image index
`sha256:13418a828f4a90f6a04654255eb2071aca5dfdf2f19b9043b2d102007c143128`.
Depot admitted that exact image. Its constrained, model-free volume and optional
component checks passed. Live staging then exposed three separate issues: local
judges returned prose that the comparison parser could not reliably interpret;
the cached asynchronous provider client crossed the live and experiment event
loops; and the review page hid an empty HTTP 429 behind a JSON parsing error.
The first application comparison was cancelled after 22 dispatches, without
candidate-arm results or a prompt promotion. An independent source/citation
check failure in its baseline remains a real failed result.

Release `0.7.3`, source `b00838289021948c89407bafd9767798d3a40456`, published
the signed image index
`sha256:d0bcbd0c7894976700f58ea8666ebed376753db3c4230651fc75d70c1e9cc35d`.
Depot verified the signature and its fresh admission passed registration, build,
spawn, seed, verification and Companion smoke checks. The exact image also
passed the constrained model-free volume and optional-component checks.
Admission projects and their dedicated credentials were cleaned up afterward.

That release required typed provider output for local judges, kept experiment
provider calls on the Companion event loop, and displayed actionable review
transport errors. Its subsequent live application rehearsal produced quality
failures or incomplete deadline/readback results and no eligible promotion
candidate. Corrected-source read-only accounting of the completed cross-provider
run found four failing quality pairs; it did not rewrite the deployed incomplete
receipt. No qualifying promotion/rollback rehearsal was completed. Successful
publication and admission therefore did not establish full-spec qualification.

The `0.8.0` Beta includes later corrections for nullable structured judge output,
complaint classification/tracing, bounded dispute follow-up, provider model
identity, cancellable asynchronous judges, and media/annotation readback. Its
new signed image, admission and live qualification remain pending. Human audio
checks, independent annotation, second-presenter acceptance and native governance
permission checks are deferred. See [Beta release notes](BETA_RELEASE_NOTES.md).
Strict criteria, evidence gates, spending authorization and model-call limits
remain in force; Beta publication does not turn failures or missing evidence into
passing results.

The original shared-core baseline was v5.0.0 at
`5f56e75f9a8b5d160ddf54a8fcde380d0d15a205`. Early banking integration used
`eb3ca1d678bc77a377d815e0bb8e5d895a4d9b22` before the coordinated stable core
release. These are historical identities, not current install or publish pins.

Package `0.4.0` covered the earlier sessions/history/assets/APM qualification
stage. Later intermediate candidates added workflow and voice functionality.
Never reuse an intermediate release's tag or signed image digest for the current
candidate; record the actual signed release receipt after publication.

## Qualification status

The immutable source revision is the release commit, emitted into image labels by CI. Record the actual release tag, signed digest, exact target, admission ID and delivered rehearsal evidence when those operations execute. The current `0.8.0` Beta is intended for guided synthetic demonstrations, with its own image, admission and live qualification pending. Earlier releases retain their recorded publication/admission outcomes and unmet live or human checks. Beta catalogue publication does not assert full-spec qualification or close those requirements.
