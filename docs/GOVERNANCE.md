# Governance qualification

Open **Governance** from the Companion. Depot’s `manage` access contract and signed
live-route proof protect this page; proofless direct-container requests are denied.
This is Depot access control, distinct from native Langfuse user roles.

**Refresh target status** makes only two supported project-key reads:
`GET /api/public/projects` and `GET /api/public/health`. Each request has a five-second
timeout and no automatic retry. Concurrent inspection is rejected. No settings,
identities, provider calls or historical seed files are changed. No manual
attestations are accepted or persisted.

| State | Meaning on this page |
| --- | --- |
| Available | The named operation or setting was read successfully. This does not prove Enterprise entitlements or user authorization. |
| Disabled | Target inspection is disabled in preview; automatic privileged qualification is disabled; or exact 4.68 readback says automatic retention is not configured. |
| Unavailable | This interface cannot establish the capability. Read its prerequisite and labelled **Documentation chapter**. |
| Failed | A read failed or returned an invalid retention value. No stale success is retained. |

A native settings link is an inspection location, not availability or permission
proof. Check time applies only to displayed readback. New targets, deployments,
versions, accounts or permissions require fresh qualification. Never reuse old
disposable-project observations.

## Retention

On **Langfuse 4.68.0**, the project API’s `retentionDays` is omitted when null or zero:
automatic event deletion is disabled. A configured positive value must be at least
three days. Other versions remain **Unavailable** until their contract is qualified.
Readback proves configuration, not actual deletion enforcement or permission to
change it. Data-access windows are distinct. Saved dataset items and audit logs
remain outside event retention; their source traces can expire. Do not shorten
retention to demonstrate the control.

Sources: [exact API schema](https://github.com/langfuse/langfuse/blob/v4.68.0/fern/apis/server/definition/projects.yml),
[exact read implementation](https://github.com/langfuse/langfuse/blob/v4.68.0/web/src/pages/api/public/projects/index.ts),
[retention documentation](https://langfuse.com/docs/administration/data-retention).

## Native roles, protected labels and audit

Project API keys are not signed-in users. Project membership can override inherited
organization roles. Plan names, metadata and configuration flags cannot prove
entitlements or effective permissions. Native RBAC, protected-label enforcement
and audit access remain **Unavailable** until separately qualified by an authorized
operator on the exact target. The page does not convert operator statements into
programmatically verified authorization.

Langfuse 4.68 allows **ordinary project API keys** to move protected labels.
Companion promotion uses such a key. Successful API promotion does not prove a
restricted native user could promote. Depot route denial likewise does not prove
native protected-label enforcement. **Prompt promotion receipts** preserve actual
API outcomes; a target denial must not be relabelled as a different actor’s role
denial. Temporary in-app-agent keys use another authorization path and are not
used here.

Sources: [RBAC](https://langfuse.com/docs/administration/rbac),
[protected labels](https://langfuse.com/docs/prompt-management/features/prompt-version-control),
[exact key authorization](https://github.com/langfuse/langfuse/blob/v4.68.0/web/src/features/prompts/server/utils/authorizeProtectedLabelMutation.ts).

Audit requires the entitlement and current user’s `auditLogs:read` permission.
Inspect existing setup or promotion events in the exact project’s native viewer.
Record actor type, operation, resource and time. Do not export unrelated
organization history or credentials. Accessible owner views prove only that
account’s access. [Audit documentation](https://langfuse.com/docs/administration/audit-logs).

## Actual-target rehearsal record

An authorized operator records evidence in the release qualification artifact,
outside the historical seed. This file pre-qualifies no current target.

1. Record target URL, project ID, installed version, deployment identity, check
   time and Companion readback. Identify each native account’s effective role.
2. Inspect the relevant native entitlement and permission with authorized accounts.
   Record the exact view and result. Unsupported or inaccessible controls remain
   **Unavailable**, with their missing prerequisites and documentation chapter.
3. Protected-label enforcement needs separately authorized administrator and
   restricted-user accounts. Configure a disposable rehearsal label only with
   explicit authority, using supported native settings. Never downgrade the sole
   owner or change production labels merely to manufacture a denial.
4. Demonstrate allowed administrator and denied restricted-user changes through
   native interfaces. Record exact prompt, version, label, actor, time, before/after
   state and actual result. Verify denial left the label unchanged. Owner-only
   evidence is insufficient; an API key is not a substitute restricted identity.
5. Correlate Companion promotion receipts with the actual API actor and native
   prompt history. Record audit visibility and inspect existing relevant events
   separately. Do not construct synthetic audit events or denials.
6. Read retention without changing it. Distinguish setting, change permission and
   deletion enforcement. Record each capability’s state, proof and limitations.
   Missing restricted-user access leaves native enforcement **Unavailable** even
   when owner settings and audit views are accessible.

Privileged operations require separate explicit authority and supported target
administration. The kit does not use organization/admin credentials, modify the
Langfuse database, provision another stack or claim compliance certification.
