"""Owned answer ratings through the bound SDK, verified against the public score read."""
from langfuse.api.scores.types.create_score_request import CreateScoreRequest


def read(client, receipt):
    result = client.api.scores.get_many(id=receipt['id'], fields='subject,details', limit=1)
    for score in result.data:
        subject = score.subject
        if (score.id == receipt['id'] and score.timestamp == receipt['timestamp']
                and score.name == 'user-thumbs' and score.source == 'API'
                and score.data_type == 'BOOLEAN' and subject is not None
                and subject.kind == 'observation' and subject.id == receipt['observation_id']
                and subject.trace_id == receipt['trace_id']
                and score.value == bool(receipt['value']) and (score.comment or '') == receipt['comment']):
            return 'saved'
    return 'pending'


def write(client, receipt):
    result = client.api.scores.create(request=[CreateScoreRequest(
        id=receipt['id'], timestamp=receipt['timestamp'], name='user-thumbs',
        trace_id=receipt['trace_id'], observation_id=receipt['observation_id'],
        value=receipt['value'], comment=receipt['comment'], data_type='BOOLEAN', source='API',
        environment=receipt['environment'], metadata={'origin': 'participant-feedback',
        'live_run_id': receipt['run_id'], 'session_id': receipt['session_id']})])
    if getattr(result, 'rejected', 0):
        raise ValueError('Feedback was rejected by the target')
    return read(client, receipt)
