# Banking candidate provenance

- Fork: https://github.com/borismichel/langfuse-synth-bank-assistant
- Upstream parent: https://github.com/doneyli/clickhouse-llm-observability
- Inspected upstream commit: `513efb14fda3efeb61179285354fb9a63ebc36d1`.
- Fork identity and unused destination checked by the integration coordinator before GitHub fork creation; upstream history is retained. No existing remote was repointed.
- Upstream Apache-2.0 licence remains in `LICENSE`; banking source and notices are preserved.
- Root package slug: `northwind-bank-assistant`; GHCR release identity: `ghcr.io/borismichel/langfuse-synth-bank-assistant`.
- Core baseline and reusable publishing workflow: v5.0.0, commit `5f56e75f9a8b5d160ddf54a8fcde380d0d15a205`.
- Runtime and authoring pin: tested integration candidate `e6f6622a630068f383f48445f957fb4eab278797`, which includes the split-generation Manifest contract, native Companion factories and protected live-route transport gate. This is an immutable integration candidate, not a stable core release. Coordinate a stable release and consumer repins before final qualification. The publishing workflow remains pinned independently to the unchanged v5.0.0 release implementation.
- Python 3.12.15 and Langfuse SDK 5.0.4; full dependency resolution in `uv.lock`.

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

## Candidate version

Package version `0.4.0` combines participant sessions/reset/feedback, deterministic history, validated history controls and progress, native datasets/prompts/score definitions, historical evidence navigation, and optional private APM. It preserves the native-provider duplicate-generation repair and corrects verification for target responses with empty non-generation usage. Its scope is exact-image, live-target and deployed-URL qualification of #367–#372 and #382; it does not represent full-spec catalogue completion. Record the signed image receipt after publication rather than reusing an earlier candidate digest.

## Qualification status

The immutable source revision is the release commit, emitted into image labels by CI. Record the actual release tag, signed digest, exact target, admission ID and delivered rehearsal evidence when those operations execute. They are pending at foundation authoring time. No full-spec or catalogue-ready status is claimed by this document.
