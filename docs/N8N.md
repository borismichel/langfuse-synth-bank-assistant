# Complaint workflow

Enable **Complaint workflow** in Configuration before deploying. Setup creates
`northwind-complaint-triage` only if the name is absent and labels its initial
version `production`. An existing prompt without that label needs an explicit
presenter decision; setup does not overwrite or relabel it. Disabled deployments
create no workflow prompt, child process, listener, export queue or retry work.
The option changes separate runtime state, not the historical spool.

Start the Companion and open **Complaint workflow**. Depot requires the owner,
an administrator or an active co-presenter, including when the participant app is
public. Open the **Complaint** node to inspect its synthetic input. Select
**Execute workflow** to run **Mask PII → Classify complaint → Draft reply**.
Inspect each node's output and the execution record. The final output includes
the actual classification, reply, Langfuse trace link and n8n execution link.
Use synthetic complaints only. The workflow classifies and drafts; it does not
block cards, open disputes, issue refunds, file complaints or send messages.

Each completed execution makes two calls to the deployment's selected primary
model. Classification fetches the current production managed prompt and records
its version; drafting uses a fixed safety instruction. Normal provider usage and
costs appear on the two LangChain generation observations. Repeating a completed
callback does not repeat its model call. The bridge allows one active step and
at most 20 new complaint executions per instance per hour. It rejects malformed
classification output rather than substituting a successful-looking result.

Native n8n workflow and node spans retain their original trace and parent IDs.
The native HTTP node propagates W3C context to the private bridge; its guardrail,
classification and draft observations join that same trace. The existing SDK
owns model instrumentation, including the existing native-provider duplicate
suppression rule. Native spans are explicitly operational `span` observations,
never generations or delivered banking answers. Their metadata marks them
ineligible for banking conduct evaluation. They cannot supply input/output,
model, usage, cost, arbitrary eligibility or customer identity attributes.
The bridge maps only previously registered executions to synthetic subjects.
Resources, events, links, trace state and status descriptions are discarded.

Masking runs before the model and Langfuse payloads. n8n's own local execution
history necessarily contains the synthetic original input. Its pruning limits
are 50 executions and one hour; pruning is periodic, not immediate deletion.
The whole instance's local SQLite state, encryption key, random owner account,
callback credential and execution history are removed on stop or expiry.
Restart creates empty workflow state. Participant conversation reset does not
clear this shared presenter evidence. Managed prompts and Langfuse traces remain
in the bound project under that project's retention/lifetime policy.

n8n 2.42.4 and its transitive dependencies are locked in
`runtime/n8n/package-lock.json`. The build uses an immutable Node 24 Debian image;
Node and the installed packages are copied into the existing non-root image.
There is no package installation at live startup. n8n retains its bundled license
files; see its [license](https://github.com/n8n-io/n8n/blob/n8n%402.42.4/LICENSE.md).
Only Manual Trigger, Set, Webhook and HTTP Request nodes are loaded. This is a
fixed complaint workshop: arbitrary Code, command execution, filesystem and
community nodes are not part of the delivered option. The task broker binds
loopback in external mode; no task runner is launched or needed.

All listeners use per-instance loopback ports. No host port or external workflow
host is created. Provider and Langfuse credentials remain in the Adapter process;
the n8n child receives only its random loopback callback credential. The native
collector authenticates that credential, limits request size to 1 MiB and exports
to the Adapter's fixed resolved target with no caller-controlled URL. It limits
native exports to 30 per minute and caches acknowledged batches. OTLP partial
success is returned unchanged and makes workflow health fail; transport failure
returns unavailable. Stop/start after resolving the cause. An ambiguous network
failure may still mean the target accepted a batch; the transport does not claim
exactly-once delivery or synthesize missing observations.

The authenticated `/n8n` route injects the random owner session server-side and
never returns its cookie. Bootstrap uses the pinned release's private
`POST /rest/owner/setup` contract; upgrades must rerun the real-image login,
editor and workflow tests. Account provisioning routes are unavailable through
the editor proxy. Public API and telemetry/templates/version notifications are
disabled. Depot's canonical public origin must be configured; absent or invalid
origin fails startup. Caller origin, cookies, proof and forwarding headers do not
become n8n credentials. The generic core proof gate and Depot wildcard gate close
direct access; the exact safe health path remains public. SSE comments every
15 seconds keep the managed HTTP route alive during idle editor sessions.

The child has a 768 MiB Node heap target, one production execution at a time and
a 120-second workflow timeout. The complete kit remains under Depot's existing
2 GiB, two CPU and 512 PID limits; no global resource limit is raised. Its private
state consumes the container's temporary filesystem. The owner can inspect
readiness and a bounded error code through `/api/health`; process exit or native
export failure cannot appear ready. Shutdown first stops n8n and flushes native
spans while the bridge remains alive, then closes unfinished observations and
the bridge. Child process groups and temporary directories are cleaned up.

The local image checks use no external network and no published ports. They
exercise real n8n import, owner bootstrap, HTML/assets, execution, native export,
private routing, SSE beyond 30 seconds, new-instance isolation, child failure and
cleanup under the unchanged resource limits. A real SDK and synthetic HTTP model
and target verify masking, exactly two generations, same-trace correlation,
idempotent callbacks and export rejection handling. Final admission additionally
requires the signed candidate through the running Depot, browser execution and
actual Langfuse readback; local fixture results do not replace that qualification.

Sources: [n8n deployment settings](https://docs.n8n.io/hosting/configuration/environment-variables/deployment/),
[isolation](https://docs.n8n.io/hosting/configuration/configuration-examples/isolation/),
[CLI](https://docs.n8n.io/hosting/cli-commands/),
[Langfuse n8n integration guidance](https://langfuse.com/integrations/no-code/n8n).
