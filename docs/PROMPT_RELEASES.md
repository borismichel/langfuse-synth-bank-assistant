# Prompt promotion and rollback

Open **Northwind Bank → Prompt releases** with Depot permission to manage the live instance. Customer chat access does not grant release access.

1. In **Application experiments**, compare exact baseline and candidate versions on an authored cohort with the **Current** retrieval policy. Record the selected application and judge models. Run the complete comparison, then **Refresh native evidence**.
2. Inspect expected, scored, missing, error and failed counts and both native experiment links. A deliberately failing regression, incomplete result or runner failure cannot authorise promotion. Each passing decision covers its recorded cohort; it does not establish full-kit qualification.
3. Open **Prompt releases**. Select the passed comparison and inspect its candidate and production-at-comparison versions. **Promote tested prompt** reads native results again and checks current production, prompt content, cohort, rubric, criteria and model policy. It changes only the tested prompt version, then reads back production and the recorded previous-production label.
4. Open a **New conversation** in the banking assistant and send a representative question. Follow its Langfuse trace. Inspect the actual generation prompt associations and current policy results. The SDK prompt cache is cleared after the transition; a container restart is unnecessary. Missing target access or fallback output does not prove adoption.
5. In **Prompt releases**, select the recorded promotion and choose **Restore previous prompt**. It refuses if production has changed since that promotion. Use another new conversation and inspect its actual prompt associations and policy results to prove rollback.

The target has no atomic compare-and-swap for labels. The controls check production immediately before and after each move, but presenters must coordinate external edits. Unknown mutation responses remain **unconfirmed**: production may have changed. Inspect native prompt history and recorded outcomes before another action. Repeating the same request identity returns its receipt and does not blindly repeat a mutation. Target permission failures remain denied.

Release receipts are private, bounded and local to the live container. They survive an application process restart in that container. Container replacement removes them; native prompt history and experiment evidence remain. A new instance cannot invent an earlier rollback receipt. Participant reset does not delete release evidence. Imported history remains read-only.

Langfuse project API credentials are distinct from interactive user roles and may move protected labels. Depot presenter authorisation is a separate boundary. Demonstrate native protected-label enforcement only with qualified restricted users; do not present an API-key promotion as proof of member or viewer restrictions.

## Local CI lesson

The fork's model-free CI exercises a passing control, business regressions and missing evidence through the application, SDK, provider HTTP fixtures and isolated MCP tools. Promotion consumes a complete fixed-cohort gate and exact native evidence; a passing script exit alone does not move production. The developer command is `uv run pytest tests/test_experiments.py tests/test_promotions.py` after the frozen developer install.

Actual model experiments, native readback and label actions run on the Depot host using its injected credentials. Do not activate upstream prompt webhooks or copy project/provider secrets into GitHub-hosted jobs. This preserves the prompt-gate lesson while keeping live evaluation and deployment credentials within the authorised runtime.
