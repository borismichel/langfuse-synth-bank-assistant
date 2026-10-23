"""Public SDK export-mask contract: owned evidence linkage and untrusted payloads."""
import json
import pytest

from langfuse.types import MaskOtelSpansParams, OtelSpanData, OtelSpanIdentifier
from northwind.masking import (CONDUCT_EVIDENCE_SCHEMA, APPLICATION_EVIDENCE_SCHEMA,
    CALIBRATION_EVIDENCE_SCHEMA, COMPLAINT_EVIDENCE_SCHEMA, VOICE_EVIDENCE_SCHEMA, mask_otel_spans)

CARD = '4111111111111111'  # Public synthetic test card, also a valid 16-hex span ID.
MARKER = 'langfuse.observation.metadata.northwind_evidence_schema'
ROOT = 'langfuse.experiment.item.root_observation_id'
OUTPUT = 'langfuse.observation.output'
INPUT = 'langfuse.observation.input'


def exported(attributes, *, scope='langfuse-sdk', name='comparison item'):
    identifier = OtelSpanIdentifier(trace_id='a'*32, span_id=CARD)
    span = OtelSpanData(trace_id=identifier.trace_id, span_id=identifier.span_id,
        parent_span_id=None, name=name, instrumentation_scope_name=scope,
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


@pytest.mark.parametrize('schema,name,owned,unowned',[
    (APPLICATION_EVIDENCE_SCHEMA,'experiment-item-task','observation_id','generation_id'),
    (CALIBRATION_EVIDENCE_SCHEMA,'experiment-item-task','generation_id','observation_id'),
    (COMPLAINT_EVIDENCE_SCHEMA,'n8n-complaint-triage','observation_id','generation_id'),
])
def test_each_output_schema_preserves_only_its_owned_links(schema,name,owned,unowned):
    forged=json.dumps({'northwind_evidence_schema':schema,owned:CARD})
    payload={owned:CARD,unowned:CARD,'answer':forged,'tool':{owned:CARD},'PIN':1234}
    attributes={MARKER:json.dumps(schema),ROOT:CARD,INPUT:forged,OUTPUT:json.dumps(payload)}
    result=exported(attributes,name=name)
    output=json.loads(result[OUTPUT])
    assert output[owned]==CARD
    assert '[REDACTED_CARD' in output[unowned]
    assert '[REDACTED_CARD' in output['answer']
    assert '[REDACTED_CARD' in output['tool'][owned]
    assert output['PIN']=='[REDACTED_SECRET]'
    assert CARD not in result[INPUT]
    for changed in ({MARKER:json.dumps('unknown-schema')},{MARKER:json.dumps({'schema':schema})},{MARKER:None}):
        assert CARD not in exported({**attributes,**changed},name=name)[OUTPUT]
    assert CARD not in exported(attributes,name=name,scope='customer-tool')[OUTPUT]
    assert CARD not in exported({**attributes,OUTPUT:json.dumps({owned:'card '+CARD})},name=name)[OUTPUT]
    if schema==COMPLAINT_EVIDENCE_SCHEMA:
        assert CARD not in exported(attributes,name='customer-tool')[OUTPUT]
    else:
        assert CARD not in exported({**attributes,ROOT:'b'*16},name=name)[OUTPUT]


def test_calibration_item_metadata_requires_reserved_sdk_experiment_provenance():
    item='langfuse.experiment.item.metadata'
    experiment='langfuse.experiment.metadata'
    owned=json.dumps({'northwind_evidence_schema':CALIBRATION_EVIDENCE_SCHEMA})
    payload=json.dumps({'source_observation_id':CARD,'customer':{'source_observation_id':CARD},'PIN':1234})
    attributes={experiment:owned,item:payload,OUTPUT:payload,INPUT:payload}
    result=exported(attributes)
    metadata=json.loads(result[item])
    assert metadata['source_observation_id']==CARD
    assert '[REDACTED_CARD' in metadata['customer']['source_observation_id']
    assert metadata['PIN']=='[REDACTED_SECRET]'
    assert CARD not in result[OUTPUT] and CARD not in result[INPUT]
    for marker in ('{}',json.dumps({'customer':json.loads(owned)}),json.dumps(owned)):
        assert CARD not in exported({**attributes,experiment:marker})[item]
    assert CARD not in exported(attributes,scope='customer-tool')[item]
    assert CARD not in exported({**attributes,item:json.dumps({'source_observation_id':'card '+CARD})})[item]


def test_voice_assistant_metadata_requires_the_marked_voice_span():
    assistant='langfuse.observation.metadata.assistant_observation_id'
    forged=json.dumps({'northwind_evidence_schema':VOICE_EVIDENCE_SCHEMA,'assistant_observation_id':CARD})
    attributes={MARKER:json.dumps(VOICE_EVIDENCE_SCHEMA),assistant:json.dumps(CARD),INPUT:forged,OUTPUT:forged,
                'langfuse.observation.metadata.customer':forged}
    result=exported(attributes,name='northwind-voice-call')
    assert json.loads(result[assistant])==CARD
    assert CARD not in result[INPUT] and CARD not in result[OUTPUT]
    assert CARD not in result['langfuse.observation.metadata.customer']
    for name,scope in (('customer-tool','langfuse-sdk'),('northwind-voice-call','customer-tool')):
        assert CARD not in exported(attributes,name=name,scope=scope)[assistant]
    assert CARD not in exported({**attributes,MARKER:None},name='northwind-voice-call')[assistant]
    assert CARD not in exported({**attributes,assistant:json.dumps('card '+CARD)},name='northwind-voice-call')[assistant]
