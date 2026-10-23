"""Model-free prompt setup, after the standard estimate confirmation."""
from langfuse.api import NotFoundError
from langfuse_synth_core.lfclient import get_langfuse
from .pipeline import check_target


def run_setup_prompts(cfg):
    from northwind.prompts import PROMPT_NAME, V5_RELEASE
    print('Checking the banking production prompt', flush=True)
    check_target(cfg)
    client = get_langfuse(cfg)
    try:
        try:
            prompt = client.get_prompt(PROMPT_NAME, label='production', max_retries=0, fetch_timeout_seconds=15)
            print(f'Existing banking production prompt retained: version {prompt.version}', flush=True)
        except NotFoundError:
            # A pre-existing named prompt without production is human-owned state.
            try:
                client.get_prompt(PROMPT_NAME, label='latest', max_retries=0, fetch_timeout_seconds=15)
            except NotFoundError:
                prompt = client.create_prompt(name=PROMPT_NAME, prompt=V5_RELEASE, type='text',
                    labels=['production'], tags=['northwind-bank', 'depot-managed'],
                    commit_message='Initial Northwind bank deployment prompt')
                print(f'Created initial banking prompt: version {prompt.version}', flush=True)
            else:
                raise ValueError('Existing banking prompt has no production label; assign it explicitly before deploying.')
        from .history_prompts import setup_history_prompts
        setup_history_prompts(cfg, client)
    finally:
        client.flush()
        client.shutdown()
    return 0
