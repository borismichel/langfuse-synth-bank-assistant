"""Operational-only projection of the existing process tracing pipeline."""
from __future__ import annotations

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, Status

_TOOLS = {'get_customer_profile', 'list_accounts', 'get_recent_transactions',
          'block_card', 'open_dispute', 'schedule_callback'}
_NAMES = {'northwind-assistant', 'generate-response', 'kb-retrieval', 'input-guardrail',
          'output-guardrail', 'LangGraph', 'agent', 'tools', 'guardrail', '__start__',
          'anthropic.messages.create', 'openai.chat.completions'} | {
              prefix + tool for prefix in ('mcp-server: ', 'mcp-client: ', 'core-banking.')
              for tool in _TOOLS}
_ENUMS = {
    'http.request.method': {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'},
    'gen_ai.provider.name': {'anthropic', 'openai'},
    'langfuse.observation.type': {'span', 'agent', 'generation', 'retriever', 'tool', 'guardrail', 'chain'},
}


def _context(context):
    if context is None:
        return None
    # Tracestate is arbitrary vendor text; IDs and flags are sufficient here.
    return SpanContext(context.trace_id, context.span_id, context.is_remote, context.trace_flags)


class OperationalExporter(SpanExporter):
    """Copy with a closed field/value allowlist; never modify the Langfuse copy.

    Unknown names become `operation`; events, links, status descriptions, resource
    extras, scope metadata and all unlisted attributes are deliberately omitted.
    No usage or cost fields are duplicated into this operational view.
    """

    def __init__(self, inner):
        self.inner = inner

    def export(self, spans):
        clean = []
        for span in spans:
            attrs = {key: value for key, value in (span.attributes or {}).items()
                     if key in _ENUMS and isinstance(value, str) and value in _ENUMS[key]}
            code = (span.attributes or {}).get('http.response.status_code')
            if type(code) is int and 100 <= code <= 599:
                attrs['http.response.status_code'] = code
            service = span.resource.attributes.get('service.name') if span.resource else None
            if service not in {'northwind-assistant', 'core-banking-mcp'}:
                service = 'northwind-assistant'
            clean.append(ReadableSpan(
                name=span.name if span.name in _NAMES else 'operation',
                context=_context(span.context), parent=_context(span.parent),
                resource=Resource({'service.name': service}), attributes=attrs,
                events=(), links=(), kind=span.kind, status=Status(span.status.status_code),
                start_time=span.start_time, end_time=span.end_time,
                instrumentation_scope=InstrumentationScope('northwind.operational'),
            ))
        return self.inner.export(clean)

    def shutdown(self):
        return self.inner.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.inner.force_flush(timeout_millis)
