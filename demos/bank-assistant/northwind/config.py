"""
Runtime configuration and the single OpenTelemetry pipeline (OBS-01, OBS-06, ENT-03).

One TracerProvider, two exporters:

    ┌────────────── app process ──────────────┐
    │  LangGraph / LangChain / MCP / our spans │
    │                 │                        │
    │         TracerProvider (W3C ids)         │
    │        ┌────────┴─────────┐              │
    │  LangfuseSpanProcessor   BatchSpanProcessor
    │  (AI spans, PII-masked)  (ALL spans, payloads stripped)
    └────────┼──────────────────┼──────────────┘
             ▼                  ▼
         Langfuse         APM (Jaeger locally)

Because both exporters hang off the same provider, a trace has ONE trace id in
both tools — the correlation key an SRE pastes from an infrastructure alert into
Langfuse, or vice versa. The APM copy carries timings, status and structure but
no prompts or completions, so customer text never reaches the APM.

Configuration and ready clients are supplied by the Depot Companion Adapter.
Workstation profiles and .env files are never read.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEMO_DIR = Path(__file__).resolve().parent.parent
PROFILE = "depot"
_adapter = None
LANGFUSE_BASE_URL = ""
PROJECT_ID = ""
ENVIRONMENT = os.environ.get("LANGFUSE_TRACING_ENVIRONMENT", "production")
RELEASE = "bank-depot-0.4.0"
SAMPLE_RATE = 1.0
AGENT_MODEL = "claude-sonnet-4-6"
JUDGE_MODEL = AGENT_MODEL
MCP_URL = "http://127.0.0.1:8765/mcp"
MCP_ENABLED = False  # #368 enables the supervised, customer-bound banking service.
APM_OTLP_ENDPOINT = "off"  # Enabled only after the supervised local service is ready.
APM_OTLP_HEADERS = ""
APM_UI_TRACE_URL = ""


def env(key, default=None):
    return os.environ.get(key, default)


def bind_adapter(adapter):
    global _adapter, LANGFUSE_BASE_URL
    _adapter = adapter
    LANGFUSE_BASE_URL = adapter.base_url


def get_adapter():
    if _adapter is None:
        raise RuntimeError("Banking runtime must be started through the Depot Companion Adapter")
    return _adapter


def native_llm_factory(*, provider, model, api_key):
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, api_key=api_key, temperature=0.2, max_retries=1, timeout=60)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, api_key=api_key, temperature=0.2, max_tokens=1024, max_retries=1, timeout=60)
    raise ValueError("Unsupported banking model provider")

def configure_apm(endpoint: str, trace_path: str = ''):
    """Only a supervised child on this process's loopback may receive spans."""
    from urllib.parse import urlsplit
    global APM_OTLP_ENDPOINT, APM_UI_TRACE_URL
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path != '/v1/traces' or parsed.query or parsed.fragment or parsed.username):
        raise ValueError('APM requires the supervised loopback receiver')
    if _provider is not None and APM_OTLP_ENDPOINT != endpoint:
        raise RuntimeError('Configure APM before constructing the shared tracer')
    APM_OTLP_ENDPOINT = endpoint
    APM_UI_TRACE_URL = trace_path


_provider = None
_langfuse = None


def tracer_provider(service_name: str = "northwind-assistant"):
    """The process-wide provider. APM processor attached first, Langfuse second."""
    global _provider
    if _provider is not None:
        return _provider
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from synth.companion.apm import OperationalExporter

    # Sampling lives on the PROVIDER. When an app passes its own provider to
    # Langfuse(tracer_provider=...), the SDK cannot install its sample_rate
    # sampler, so we do it here. ParentBased: a downstream service (the MCP
    # server) follows the caller's decision via the traceparent flags, and the
    # SDK samples scores with this same sampler, so a sampled-out turn leaves no
    # orphan scores. This samples the APM copy too; to keep 100% in the APM while
    # sampling Langfuse, sample in an OTel Collector instead.
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    sampler = ParentBased(TraceIdRatioBased(SAMPLE_RATE)) if SAMPLE_RATE < 1 else None
    _provider = TracerProvider(sampler=sampler, resource=Resource.create({
        "service.name": service_name,
        "service.version": RELEASE,
        "deployment.environment": ENVIRONMENT,
    }))
    if APM_OTLP_ENDPOINT and APM_OTLP_ENDPOINT.lower() not in ("none", "off", "disabled"):  # CI: no APM
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        import requests
        local_session = requests.Session()
        local_session.trust_env = False
        headers = {}
        _provider.add_span_processor(BatchSpanProcessor(
            OperationalExporter(OTLPSpanExporter(endpoint=APM_OTLP_ENDPOINT, headers=headers, timeout=2, session=local_session)),
            schedule_delay_millis=500, max_queue_size=256, max_export_batch_size=64))
    trace.set_tracer_provider(_provider)
    return _provider


def _export_ai_span(span):
    """Keep one generation per callback-owned call, preserving SDK defaults elsewhere."""
    from langfuse import is_default_export_span

    scope = span.instrumentation_scope
    callback_owned = (span.attributes or {}).get(
        "langfuse.trace.metadata.generation_owner") == '"langchain"'
    if callback_owned and scope and scope.name == "com.anthropic.sdk.python":
        return False
    return is_default_export_span(span)


def langfuse_factory(*, host, public_key, secret_key, service_name="northwind-assistant"):
    """Langfuse client bound to the shared provider, with masking + sampling."""
    global _langfuse
    if _langfuse is not None:
        return _langfuse
    from langfuse import Langfuse

    from northwind import masking

    provider = tracer_provider(service_name)
    _langfuse = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=host,
        environment=ENVIRONMENT,
        release=RELEASE,
        sample_rate=SAMPLE_RATE,
        tracer_provider=provider,
        mask_otel_spans=masking.mask_otel_spans,
        should_export_span=_export_ai_span,
    )
    return _langfuse


def get_langfuse(service_name="northwind-assistant"):
    return get_adapter().langfuse()


def shutdown():
    flush()
    if _langfuse is not None:
        _langfuse.shutdown()
    if _provider is not None:
        _provider.shutdown()


def flush() -> None:
    """Flush BOTH exporters. Langfuse's flush alone misses the APM processor."""
    if _langfuse is not None:
        _langfuse.flush()
    if _provider is not None:
        _provider.force_flush()


# Cross-process context = W3C `traceparent` AND W3C `baggage`. In Langfuse v4 every
# observation carries its own trace attributes, so the ones a downstream service
# needs (session, user, trace name, version, environment — see agent.run_turn) must
# cross the process boundary with the span context. Baggage also carries the SDK's
# "this trace already has a root" claim; without it the receiving process marks its
# first span as a second root of the trace.
_CARRIER_KEYS = ("traceparent", "tracestate", "baggage")


def _propagator():
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    return CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


def inject_trace_context() -> dict:
    """Carrier for a call into one of OUR services (the MCP server's `_meta`)."""
    carrier: dict = {}
    _propagator().inject(carrier)
    return carrier


def extract_trace_context(carrier: dict):
    """OTel context from a carrier written by inject_trace_context()."""
    return _propagator().extract({k: v for k, v in carrier.items() if k in _CARRIER_KEYS})


_project_id: Optional[str] = None


def project_id() -> str:
    """The project id behind these keys (a cuid on Cloud), resolved once."""
    global _project_id
    if _project_id is None:
        try:
            _project_id = api("GET", "/api/public/projects")["data"][0]["id"]
        except Exception:  # noqa: BLE001
            return ""
    return _project_id


def trace_url(trace_id: str) -> str:
    pid = project_id()
    return f"{LANGFUSE_BASE_URL}/project/{pid}/traces/{trace_id}" if pid else ""


def apm_url(trace_id: str) -> str:
    return APM_UI_TRACE_URL.format(trace_id=trace_id)


def api(method, path, body=None, timeout=30, params=None):
    if method != "GET":
        raise RuntimeError("This operation requires a delivered presenter capability")
    return get_adapter().read_json(path, params=params)
