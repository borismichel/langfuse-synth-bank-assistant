"""Run inside the candidate image with no network beyond container loopback."""
import asyncio
import json
import os
from pathlib import Path

import httpx
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from synth.companion.apm import OperationalExporter
from synth.companion.jaeger import JaegerService


async def main():
    service = JaegerService(enabled=True)
    await service.start()
    assert service.ready, service.error
    for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if fields[3] == '0A':
            assert fields[1].split(':')[0] != '00000000', 'APM opened a non-loopback listener'
    assert Path('/sys/fs/cgroup/memory.max').read_text().strip() == '2147483648'
    assert Path('/sys/fs/cgroup/pids.max').read_text().strip() == '512'
    directory = Path(service.directory.name)
    provider = TracerProvider(resource=Resource({'service.name': 'northwind-assistant'}))
    provider.add_span_processor(SimpleSpanProcessor(OperationalExporter(OTLPSpanExporter(endpoint=service.otlp_url))))
    with provider.get_tracer('private-CUSTOMER-SECRET').start_as_current_span('generate-response') as span:
        span.set_attribute('customer', 'CUSTOMER-SECRET')
        span.add_event('CUSTOMER-SECRET')
        trace_id = f'{span.get_span_context().trace_id:032x}'
    provider.force_flush()
    async with httpx.AsyncClient(trust_env=False) as client:
        async with asyncio.timeout(10):
            while True:
                response = await client.get(service.query_url + '/apm/api/v3/traces/' + trace_id)
                if response.status_code == 200 and response.json().get('result'):
                    break
                await asyncio.sleep(.1)
        assert response.json()['result']['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['traceId'] == trace_id
        assert 'CUSTOMER-SECRET' not in response.text
        page = await client.get(service.query_url + '/apm/')
        assert page.status_code == 200 and '<html' in page.text.lower()
    provider.shutdown()
    await service.stop()
    assert not service.ready and service.process.returncode is not None
    assert not directory.exists()
    # A second instance has separate ports/storage and a crashed child is observed.
    other = JaegerService(enabled=True)
    await other.start()
    assert other.ready and other.query_url != service.query_url
    async with httpx.AsyncClient(trust_env=False) as client:
        absent = await client.get(other.query_url + '/apm/api/v3/traces/' + trace_id)
        assert absent.status_code == 404
    other.process.kill()
    await other.monitor
    assert not other.ready and other.error == 'APMProcessExited'
    await other.stop()
    print(json.dumps({'stored_trace_identity': True, 'payload_absent': True, 'ui_html': True,
                      'isolated_storage': True, 'failure_reported': True, 'cleanup': True,
                      'uid': os.getuid(), 'peak_memory_bytes': int(Path('/sys/fs/cgroup/memory.peak').read_text())}))

asyncio.run(main())
