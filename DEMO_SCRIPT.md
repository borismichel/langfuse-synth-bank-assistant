# Northwind Bank Assistant — Presenter Runbook

Synthetic card-dispute evidence for a banking operations audience. The frozen scenario compares the same customer's report against current and retired bank policies. The current policy follows upstream Northwind knowledge article KB-102; the retired 14-day policy is a deliberately incorrect fixture. No actual customer data or live model response is included.

## Walkthrough

1. Open **Baseline** below. Read the customer's report, retrieved current policy and answer. The report is within the synthetic 60-day window. The assistant offers an investigation without guaranteeing a refund; `policy-correctness` is 1.
2. Open **Outdated policy**. Compare the same report and customer with the retired 14-day document. The assistant incorrectly refuses the investigation; `policy-correctness` is 0.
3. Open **Corrected policy**. Confirm that retrieval selects the current policy and the answer offers an investigation without promising a refund; `policy-correctness` is 1. Compare the retrieval and answer observations and their timestamps.

The scores, token usage and costs are frozen examples, not results from live judges or incurred model spend. All three examples share one comparison session; each answer has its own observation score.

## Candidate capabilities

Available: model-free history, target validation, volume confirmation, split generation/import, exact-run verification and this target-specific runbook.

Pending under Depot spec #365: live bilingual chat, banking actions, customer isolation, feedback, full history, managed judges and conduct aggregation, annotations, experiments, prompt promotion, provider comparisons, voice, n8n, APM, governance and full operational qualification. This foundation is not the full-spec catalogue release.

## Deployment and recovery

Deploy through Depot and confirm the estimate before generation/import. Depot measures the actual spool before import. The seed makes no provider calls; Langfuse may charge for ingestion under the target plan.

Use the target links added after import. If verification fails, inspect the deployment log and target read access. Missing current-run records cannot be replaced with unrelated old project data.

Stopping the deployment removes local processes and state; Langfuse observations remain. Import is append-only. A partial import must not be retried. Use a fresh disposable project and deployment, or obtain authorisation for a complete project-data reset before a new deployment. Reset can remove human annotations and other project data; the kit never performs that reset automatically.
