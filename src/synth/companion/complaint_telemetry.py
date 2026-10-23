"""Closed native-workflow projection; operational spans cannot become answer subjects."""
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

_NODE_NAMES = {'Run complaint', 'Complaint', 'Complaint webhook', 'Mask PII', 'Classify complaint', 'Draft reply'}


def project_native(raw, runs):
    incoming = ExportTraceServiceRequest.FromString(raw)
    known = {run['trace_id']: run for run in runs.values()}
    outgoing = ExportTraceServiceRequest()
    resource = outgoing.resource_spans.add()
    resource.resource.attributes.add(key='service.name', value={'string_value': 'northwind-n8n'})
    scope = resource.scope_spans.add()
    scope.scope.name = 'northwind.n8n.native'
    count = 0
    for group in incoming.resource_spans:
        for original_scope in group.scope_spans:
            for original in original_scope.spans:
                count += 1
                if count > 256:
                    raise ValueError('Native batch exceeds span limit')
                run = known.get(original.trace_id.hex())
                if (not run or original.name not in {'workflow.execute', 'node.execute'}
                        or len(original.span_id) != 8 or not any(original.span_id)):
                    raise ValueError('Native span is not a registered complaint execution')
                attrs = {a.key: a.value.string_value for a in original.attributes}
                if (attrs.get('n8n.workflow.id', 'northwindComplaint') != 'northwindComplaint'
                        or attrs.get('n8n.execution.id', run['execution_id']) != run['execution_id']):
                    raise ValueError('Native workflow identity differs')
                span = scope.spans.add(name=original.name, trace_id=original.trace_id,
                    span_id=original.span_id, parent_span_id=original.parent_span_id,
                    start_time_unix_nano=original.start_time_unix_nano,
                    end_time_unix_nano=original.end_time_unix_nano, kind=original.kind)
                span.status.code = original.status.code
                fixed = {'langfuse.trace.name': 'n8n-complaint-triage', 'langfuse.observation.type': 'span',
                    'langfuse.user.id': run['customer'], 'langfuse.session.id': run['session'],
                    'langfuse.observation.metadata.origin': 'n8n-native',
                    'langfuse.observation.metadata.conduct_eligible': 'false',
                    'langfuse.observation.metadata.n8n_execution_id': run['execution_id']}
                if attrs.get('n8n.node.name') in _NODE_NAMES:
                    fixed['langfuse.observation.metadata.n8n_node'] = attrs['n8n.node.name']
                for key, value in fixed.items():
                    span.attributes.add(key=key, value={'string_value': value})
                tags = span.attributes.add(key='langfuse.trace.tags')
                for value in ('channel:n8n', 'team:customer-care'):
                    tags.value.array_value.values.add(string_value=value)
    if not count:
        return b''
    return outgoing.SerializeToString()
