# Coding-agent workshop

This is a separate advanced extension. The normal presenter route in the delivered Presenter Runbook requires neither a coding agent nor a local terminal. Use the same approved synthetic project, fixed cohorts, prompt versions and exact observation links as that route. The upstream two-hour banking workshop is retained in `demos/bank-assistant/DEMO_SCRIPT.md` for provenance; its workstation launchers and separate-stack instructions are not Depot deployment steps.

## Access and execution boundaries

Project-data access lets an authorised agent read or change native traces, prompts, datasets and evaluation resources according to its granted credentials. It does not let the agent execute this banking application, reach the private MCP service, confirm a customer's action or obtain Depot's runtime credentials. Langfuse project API keys are not user identities or proof of restricted-user governance. Depot management access protects paid presenter controls separately.

Application execution occurs in the delivered Companion. Its LangGraph retrieval, input/output guardrails, private MCP tools and isolated synthetic customer state are the application under test. A prompt-only native experiment or a coding-agent answer does not reproduce those paths. Use **Application experiments** for the complete frozen app comparison and the customer interface for fresh-release validation.

## Workshop sequence

| Exercise | Agent task and permitted surface | Presenter proof |
| --- | --- | --- |
| Inspect a failure | Give the agent the exact synthetic trace and source-policy links from this deployment. With explicitly authorised project-data access, ask it to identify the failing lookup window or unsupported answer. | Compare its explanation with the actual tool arguments, authoritative evidence and delivered answer. Historical fixture scores are not independent human truth. |
| Propose a prompt change | Ask for a proposed candidate preserving bank policy, formal Spanish, explicit action confirmation and guardrails. Review the text before saving a new unlabelled prompt version through the authorised native interface. | Keep production unchanged. Record the exact version/content and why the change should help. |
| Compare the application | Run the delivered fixed-cohort comparison with exact baseline and candidate versions and the same judge/model policy. The coding agent may analyse the returned native result links. | Require full denominators, source/subject provenance, measured tokens and latency, and actual native readback. Unknown values remain unknown. |
| Gate the release | Inspect a passing control and an intended regression. Ask the agent to explain why the measured gate permits or blocks the tested candidate. | The gate result, not the agent's recommendation, determines eligibility. A single cohort does not qualify the whole kit. |
| Promote and roll back | The authorised presenter uses **Prompt releases**, then sends fresh conversations before and after rollback. | Exact actual prompt associations and current policy outcomes support both transitions; no application restart. |
| Calibrate judges | Compare constructed references and independently completed native human annotations; run the matched single/split chapter over identical answers/evidence/criteria. | Report disagreements, missed violations, false alarms, invalid/unresolved outputs, usage and latency with their actual denominators. Do not call the agent's labels independent SME review. |

## Local prompt-gate lesson

GitHub checks run model-free contract fixtures and package tests. They do not receive Depot runtime keys or execute paid target experiments. The delivered application comparison is the paid local/Depot gate. Both are required evidence, but neither substitutes for the other. A developer may inspect the fork's locked tests and reproduce its documented authoring check; installing a skill or CLI is not required for the presenter route.

Do not paste runtime secrets into an agent conversation, workflow file, source repository or screenshot. Provision separate approved credentials only when the workshop actually requires project-data access. Scope them to the intended synthetic target and use the organisation's normal secret handling. Do not expose the private banking MCP port to make it reachable by a coding agent.

## Governance and cleanup

Use the Governance chapter to distinguish native user-role enforcement from ordinary project-key access and Depot presenter permissions. Do not downgrade the sole owner, change retention, modify the Langfuse database or launch another stack merely to stage a denial. If a qualified restricted identity is unavailable, label that exercise documentation-only.

At the end, pause owned managed evaluation rules, stop the deployment and retain the evidence. Remove disposable admission projects and their dedicated credentials through the authorised host cleanup workflow. Stopping local services does not delete remote traces, annotations or queued evaluator work. Preserve unrelated projects and completed reviews.
