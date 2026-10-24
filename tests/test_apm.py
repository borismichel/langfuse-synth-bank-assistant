"""APM export is a second view of existing spans, with no customer payload."""
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Link, SpanContext, TraceFlags, TraceState, Status, StatusCode

from synth.companion.apm import OperationalExporter


def test_apm_preserves_trace_identity_and_timings_without_any_payload_location():
    original, apm = InMemorySpanExporter(), InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({'service.name': 'northwind-assistant',
                                                'service.version': 'CUSTOMER-SECRET',
                                                'secret': 'CUSTOMER-SECRET'}))
    provider.add_span_processor(SimpleSpanProcessor(original))
    provider.add_span_processor(SimpleSpanProcessor(OperationalExporter(apm)))
    context = SpanContext(11, 12, True, TraceFlags(1), TraceState([('private', 'CUSTOMER-SECRET')]))
    tracer = provider.get_tracer('CUSTOMER-SECRET', 'CUSTOMER-SECRET', schema_url='CUSTOMER-SECRET')
    with tracer.start_as_current_span('CUSTOMER-SECRET', links=[Link(context, {'secret': 'CUSTOMER-SECRET'})]) as parent:
        with tracer.start_as_current_span('generate-response') as span:
            span.set_attributes({'input.value': 'CUSTOMER-SECRET', 'custom-new-field': 'CUSTOMER-SECRET',
                                 'http.request.method': 'POST', 'http.response.status_code': 200,
                                 'gen_ai.provider.name': 'CUSTOMER-SECRET',
                                 'langfuse.observation.type': 'generation'})
            span.add_event('CUSTOMER-SECRET', {'secret': 'CUSTOMER-SECRET'})
            span.set_status(Status(StatusCode.ERROR, 'CUSTOMER-SECRET'))
    provider.force_flush()
    emitted, source = apm.get_finished_spans(), original.get_finished_spans()
    assert len(emitted) == len(source) == 2
    for clean, raw in zip(emitted, source):
        assert clean.context.trace_id == raw.context.trace_id
        assert clean.context.span_id == raw.context.span_id
        assert clean.start_time == raw.start_time and clean.end_time == raw.end_time
        assert (clean.parent.span_id if clean.parent else None) == (raw.parent.span_id if raw.parent else None)
        assert 'CUSTOMER-SECRET' not in clean.to_json()
        assert 'CUSTOMER-SECRET' in raw.to_json()
    assert emitted[0].name == 'generate-response'
    assert emitted[0].attributes == {'http.request.method': 'POST', 'http.response.status_code': 200,
                                     'langfuse.observation.type': 'generation'}
    assert emitted[0].status.status_code == StatusCode.ERROR
    assert emitted[1].name == 'operation'
    provider.shutdown()


def test_disabled_service_has_no_process_and_missing_dependency_is_reported():
    import asyncio
    from synth.companion.jaeger import JaegerService

    async def exercise():
        disabled = JaegerService(enabled=False, binary='/definitely/missing')
        await disabled.start()
        assert disabled.process is None and disabled.error is None
        assert disabled.ready is False
        await disabled.stop()
        enabled = JaegerService(enabled=True, binary='/definitely/missing')
        await enabled.start()
        assert enabled.ready is False and enabled.error == 'APMDependencyUnavailable'
        assert enabled.process is None
        await enabled.stop()
    asyncio.run(exercise())


def test_apm_option_is_persisted_by_delivered_generate_command_without_changing_trace_spool(tmp_path):
    import os
    import subprocess
    import sys
    import json
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'TMPDIR', 'LANG'}}
    env.update(PYTHONPATH='src:demos/bank-assistant', SYNTH_STATE_DIR=str(tmp_path/'state'), SYNTH_OUT_DIR=str(tmp_path/'out'))
    result = subprocess.run([sys.executable, '-m', 'synth.cli', 'generate-spool', '--config', 'config/demo.yaml',
                             '--set', 'runtime.apm_enabled=true'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path/'state/runtime-config.json').read_text()) == {'apm_enabled': True, 'n8n_enabled': False, 'voice_enabled': False}
    assert b'apm_enabled' not in (tmp_path/'state/events.ndjson').read_bytes()
