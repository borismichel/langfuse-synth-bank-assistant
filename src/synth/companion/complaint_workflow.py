"""Pinned low-code complaint journey; executable Code nodes are not needed."""

WORKFLOW_ID = 'northwindComplaint'
CREDENTIAL_ID = 'northwindCallback'


def workflow(bridge_url):
    nodes = [
        {'id': 'manual', 'name': 'Run complaint', 'type': 'n8n-nodes-base.manualTrigger',
         'typeVersion': 1, 'position': [0, 0], 'parameters': {}},
        {'id': 'sample', 'name': 'Complaint', 'type': 'n8n-nodes-base.set',
         'typeVersion': 3.4, 'position': [220, 0], 'parameters': {'assignments': {'assignments': [
             {'id': 'text', 'name': 'text', 'value': 'I was charged the monthly card fee twice. Please review the duplicate charge.', 'type': 'string'},
             {'id': 'customer', 'name': 'customer_id', 'value': 'C-1001', 'type': 'string'},
             {'id': 'channel', 'name': 'channel', 'value': 'web', 'type': 'string'},
         ]}, 'options': {}}},
        {'id': 'webhook', 'name': 'Complaint webhook', 'type': 'n8n-nodes-base.webhook',
         'typeVersion': 2, 'position': [220, 240], 'webhookId': 'northwind-complaint',
         'parameters': {'httpMethod': 'POST', 'path': 'complaint-triage', 'responseMode': 'lastNode', 'options': {}}},
    ]
    for index, (stage, name) in enumerate((('mask', 'Mask PII'), ('classify', 'Classify complaint'), ('reply', 'Draft reply'))):
        nodes.append({'id': stage, 'name': name, 'type': 'n8n-nodes-base.httpRequest',
            'typeVersion': 4.2, 'position': [460 + index * 240, 0],
            'credentials': {'httpHeaderAuth': {'id': CREDENTIAL_ID, 'name': 'Scoped complaint callback'}},
            'parameters': {'method': 'POST', 'url': bridge_url + '/' + stage,
                'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
                'sendBody': True, 'specifyBody': 'json',
                'jsonBody': '={{ JSON.stringify({execution_id: $execution.id, complaint: $json.body ?? $json}) }}' if stage == 'mask' else '={{ JSON.stringify($json) }}',
                'options': {'timeout': 65000}}})
    connections = {}
    for source, target in [('Run complaint', 'Complaint'), ('Complaint', 'Mask PII'),
            ('Complaint webhook', 'Mask PII'), ('Mask PII', 'Classify complaint'),
            ('Classify complaint', 'Draft reply')]:
        connections[source] = {'main': [[{'node': target, 'type': 'main', 'index': 0}]]}
    return {'id': WORKFLOW_ID, 'name': 'Complaint triage (Northwind)', 'nodes': nodes,
        'connections': connections, 'active': False, 'settings': {'executionOrder': 'v1',
        'executionTimeout': 120, 'saveDataSuccessExecution': 'all', 'saveDataErrorExecution': 'all',
        'saveManualExecutions': True}}


def credentials(token):
    return [{'id': CREDENTIAL_ID, 'name': 'Scoped complaint callback', 'type': 'httpHeaderAuth',
             'data': {'name': 'X-Northwind-Workflow', 'value': token}}]
