# APM

Enable **APM** in the deployment configuration before generating history. The
validated boolean travels through the existing read-only spool mount; it adds no
history traces, model calls or setup pipeline stages. Missing state defaults to
disabled. Start the Companion, make a banking request, and use its **Open in APM**
link. Sign in to Depot as the owner, an administrator or an active co-presenter.
The trace ID is the same as **Open in Langfuse**. The APM link is private even when
the participant app is public. It never contains credentials.

Jaeger 2.22.0 runs as a supervised child in the same non-root container. Its
query, query-gRPC and OTLP/HTTP listeners bind only to loopback; no host port,
external collector, default password or separate service is required. The query
UI and v3 read API are exposed through `/apm` with GET requests only. AI/MCP,
OTLP proxy, remote sampling, profiling and metrics listeners are not enabled.
Jaeger's own tracing uses `OTEL_TRACES_SAMPLER=always_off`, matching its release's
integration harness, so query traffic is not exported to a default collector.
The binary is copied from an immutable multi-platform official image digest;
its Apache license and notice accompany this package.

Both APM exporters attach to the existing process providers: one in the
Companion and one in its private MCP child. W3C trace context keeps their trace
identity and parent structure aligned with Langfuse. The MCP child receives only
the local OTLP destination, never a provider or portal credential. There is no
second model pipeline and no APM usage or cost accounting.

The APM copy retains IDs, parent IDs, timings, span kind and status code. It
allows only fixed operation names, a fixed service identity and a short set of
enumerated operational attribute values (plus numeric HTTP response status).
Unknown operation names become `operation`. All other attributes, event bodies,
links, status descriptions, resource extras, instrumentation-scope metadata and
trace state are omitted. This covers future SDK payload attributes as well as
known inputs, outputs, tool arguments, customer IDs and exceptions. The Langfuse
copy remains available with the kit's existing masking and complete AI context.

Memory storage retains at most 1,000 traces. Jaeger uses a 256 MiB memory limiter
and Go memory target with two Go scheduler threads; the whole live container
retains Depot's existing 2 GiB, two CPU and 512 PID limits. These are demo bounds,
not durable storage. Stopping, restarting or expiring the live instance discards
APM data and terminates its child. A new live instance has separate ports and
empty storage. Participant conversation reset does not erase shared APM evidence.

`/api/health` reports APM enabled/readiness and a bounded error code. A missing
binary, missing private-routing setup, failed startup or child exit must not
appear ready. The APM route returns unavailable if its child dies. Stop/start the
live instance after repairing its configuration; there is no detached recovery
service. Disabled APM creates no process, monitor, exporter queue or retry work.

Depot must supply the released protected-route contract and a dedicated signing
secret to its API and worker. Its core Adapter consumes the per-instance transport
proof before application code. Direct wildcard and direct container requests are
closed; the exact safe GET/HEAD health path is the exception. Rotating the host
signing secret requires stop/start of protected live instances. See Depot's
`docs/LIVE_PROTECTED_ROUTES.md` for this reusable platform capability.

The optional service tests run inside the candidate image with no external
network, no published ports, read-only root, bounded temporary storage and the
same resource limits. They query a stored trace with sensitive sentinels, check
identity/payload absence, serve the delivered HTML/query route, reject direct
access, observe child failure and verify stop cleanup. Full admission additionally
requires the signed released image through the running Depot and a browser trace
link correlated with actual Langfuse readback.

Dynatrace operation remains documentation-only. An external collector is not a
runtime option in this kit. Adding one requires a separate authorization,
credential, egress and data-filtering contract and qualification.

Sources: [Jaeger configuration](https://www.jaegertracing.io/docs/2.22/deployment/configuration/),
[memory storage](https://www.jaegertracing.io/docs/2.22/storage/memory/),
[2.22.0 image source](https://github.com/jaegertracing/jaeger/blob/v2.22.0/cmd/jaeger/Dockerfile),
[2.22.0 query routes](https://github.com/jaegertracing/jaeger/blob/v2.22.0/cmd/jaeger/internal/extension/jaegerquery/internal/server_test.go),
[2.22.0 telemetry harness](https://github.com/jaegertracing/jaeger/blob/v2.22.0/cmd/jaeger/internal/integration/integration_harness.go).
