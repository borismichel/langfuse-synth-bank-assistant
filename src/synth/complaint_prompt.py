"""Managed classification prompt, created only for the enabled workflow option."""
from langfuse.api import NotFoundError

PROMPT_NAME = 'northwind-complaint-triage'
PROMPT = '''You are the complaint-triage assistant of Northwind Bank, a fictional retail bank.
Classify the customer's complaint. Return only a JSON object with these keys:
category: card_fraud, fees, transfers, service, or other.
severity: low, medium, or high.
regulatory_escalation: boolean; true for a request to file a formal complaint, a regulator,
ombudsman or lawyer, alleged discrimination, unauthorised transactions, or unresolved loss above USD 500.
rationale: one short sentence, at most 300 characters.
Card fraud means unauthorised transactions or compromised cards; fees includes interest, overdraft and FX fees;
transfers means missing, delayed or wrong payments; service means branch, app or call-centre experience.
High severity means money currently at risk, vulnerability or regulatory threat; medium means money involved
without immediate risk; low means inconvenience. Identifiers were masked; never reconstruct them.
This is classification only. Do not claim that any banking action has occurred.'''


def setup(client):
    try:
        return client.get_prompt(PROMPT_NAME, label='production', max_retries=0, fetch_timeout_seconds=10)
    except NotFoundError:
        try:
            client.get_prompt(PROMPT_NAME, label='latest', max_retries=0, fetch_timeout_seconds=10)
        except NotFoundError:
            return client.create_prompt(name=PROMPT_NAME, prompt=PROMPT, type='text',
                labels=['production'], tags=['northwind-bank', 'depot-managed', 'n8n'],
                commit_message='Complaint workflow classification; model supplied by deployment')
        raise ValueError('Complaint prompt exists without production label; assign it before enabling the workflow.') from None
