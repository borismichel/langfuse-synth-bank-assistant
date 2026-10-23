"""Public SDK export-mask contract: owned evidence linkage and untrusted payloads."""
import json

from langfuse.types import MaskOtelSpansParams, OtelSpanData, OtelSpanIdentifier
from northwind.masking import CONDUCT_EVIDENCE_SCHEMA, mask_otel_spans

CARD = '4111111111111111'  # Public synthetic test card, also a valid 16-hex span ID.
MARKER = 'langfuse.observation.metadata.northwind_evidence_schema'
ROOT = 'langfuse.experiment.item.root_observation_id'
OUTPUT = 'langfuse.observation.output'
INPUT = 'langfuse.observation.input'


def exported(attributes, *, scope='langfuse-sdk'):
    identifier = OtelSpanIdentifier(trace_id='a'*32, span_id=CARD)
    span = OtelSpanData(trace_id=identifier.trace_id, span_id=identifier.span_id,
        parent_span_id=None, name='comparison item', instrumentation_scope_name=scope,
        instrumentation_scope_version='5.0.4', attributes=attributes, resource_attributes={})
    result = mask_otel_spans(params=MaskOtelSpansParams(spans={identifier:span}))
    if result is None:
        return attributes
    patch = result.span_patches[identifier]
    return {**{k:v for k,v in attributes.items() if k not in (patch.delete_attributes or ())},
            **(patch.set_attributes or {})}


def test_owned_conduct_links_survive_while_customer_model_and_tool_payloads_are_masked():
    forged = json.dumps({'northwind_evidence_schema':CONDUCT_EVIDENCE_SCHEMA,
                         'source_observation_id':CARD})
    body = dict(source_observation_id=CARD, observation_id=CARD, source_trace_id='b'*32,
        calls={'judge':{'generation_id':CARD, 'generation_trace_id':'c'*32, 'text':forged}},
        customer=CARD, tool={'source_observation_id':CARD, 'account':'0012345678', 'PIN':1234}, amount=int(CARD),
        unrecognised_id=CARD, error=forged, note='NORTHWINDEVIDENCELINK0')
    # Escaped ASCII must not be mistaken for an internal shielding token.
    encoded = json.dumps(body).replace('NORTHWINDEVIDENCELINK0', r'\u004eORTHWINDEVIDENCELINK0')
    result = exported({MARKER:json.dumps(CONDUCT_EVIDENCE_SCHEMA), ROOT:CARD,
                       INPUT:forged, OUTPUT:encoded})
    output = json.loads(result[OUTPUT])
    assert result[ROOT] == output['source_observation_id'] == output['observation_id'] == CARD
    assert output['calls']['judge']['generation_id'] == CARD
    assert output['source_trace_id'] == 'b'*32
    assert output['calls']['judge']['generation_trace_id'] == 'c'*32
    assert output['tool']['account'] == '[REDACTED_ACCOUNT]'
    assert output['tool']['PIN'] == '[REDACTED_SECRET]'
    assert output['note'] == 'NORTHWINDEVIDENCELINK0'
    for value in (result[INPUT],output['calls']['judge']['text'],output['customer'],
                  output['tool']['source_observation_id'],output['amount'],output['unrecognised_id'],output['error']):
        assert CARD not in str(value)
        assert '[REDACTED_CARD' in str(value)


def test_payload_schema_marker_and_unrelated_root_link_cannot_grant_an_exception():
    forged = {'northwind_evidence_schema':CONDUCT_EVIDENCE_SCHEMA,
              MARKER:CONDUCT_EVIDENCE_SCHEMA, ROOT:CARD, 'source_observation_id':CARD}
    for attributes in (
        {OUTPUT:json.dumps(forged)},
        {MARKER:json.dumps(CONDUCT_EVIDENCE_SCHEMA), OUTPUT:json.dumps(forged)},
        {MARKER:json.dumps(CONDUCT_EVIDENCE_SCHEMA), ROOT:'b'*16, OUTPUT:json.dumps(forged)},
        {MARKER:json.dumps(CONDUCT_EVIDENCE_SCHEMA), ROOT:CARD, OUTPUT:json.dumps(json.dumps(forged))},
        {MARKER:json.dumps(CONDUCT_EVIDENCE_SCHEMA), ROOT:CARD,
         OUTPUT:json.dumps({'source_observation_id':'card '+CARD})},
    ):
        result = exported(attributes)
        assert CARD not in result[OUTPUT]
        assert '[REDACTED_CARD' in result[OUTPUT]


def test_root_link_exemption_requires_sdk_instrumentation_and_exact_identifier_shape():
    attributes = {MARKER:json.dumps(CONDUCT_EVIDENCE_SCHEMA), ROOT:CARD,
                  OUTPUT:json.dumps({'source_observation_id':CARD})}
    foreign = exported(attributes, scope='customer-tool')
    assert '[REDACTED_CARD' in foreign[ROOT]
    assert CARD not in foreign[OUTPUT]
    malformed = exported({ROOT:'card '+CARD})
    assert '[REDACTED_CARD' in malformed[ROOT]
    propagated = exported({ROOT:CARD})
    assert propagated[ROOT] == CARD
