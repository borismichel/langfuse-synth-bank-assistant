"""Resolve frozen prompt versions after confirmation, without changing offline fixtures."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from langfuse.api import NotFoundError
from langfuse_synth_core.lfclient import get_langfuse
from .materialize import fixture
from .receipt import RunReceipt


def prompts():
    return {key: dict(name=f'northwind-history-{key}-' + hashlib.sha256(text.encode()).hexdigest()[:12],
                      text=text) for key, text in fixture()['prompts'].items()}


def bindings_path():
    return Path(RunReceipt.state_dir()) / 'history-prompt-bindings.json'


def setup_history_prompts(cfg, client):
    """Only absent content-addressed prompts are created. Never overwrite existing content."""
    bindings = {}
    expected_prompts = prompts()
    total = len(expected_prompts)
    for index, (key, expected) in enumerate(expected_prompts.items(), 1):
        print(f'Resolving historical prompt {index}/{total}: {key}', flush=True)
        try:
            prompt = client.get_prompt(expected['name'], label='latest', cache_ttl_seconds=0,
                                       max_retries=0, fetch_timeout_seconds=15)
        except NotFoundError:
            prompt = client.create_prompt(name=expected['name'], prompt=expected['text'], type='text',
                tags=['northwind-bank', 'depot-managed', 'seeded-fixture'],
                commit_message='Frozen historical prompt; synthetic fixture, never production')
        if prompt.prompt != expected['text']:
            raise ValueError(f'Historical prompt content collision: {expected["name"]}; no overwrite performed')
        bindings[key] = {'name': expected['name'], 'version': prompt.version}
        print(f'Resolved historical prompt {index}/{total}: version {prompt.version}', flush=True)
    path = bindings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'base_url': cfg.target.base_url,
        'public_key': os.environ['LANGFUSE_PUBLIC_KEY'], 'bindings': bindings}, sort_keys=True))
    print('Frozen history prompt versions resolved', flush=True)


def read_bindings(cfg):
    try:
        saved = json.loads(bindings_path().read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('Run setup-prompts for this target before importing history') from exc
    if saved['base_url'] != cfg.target.base_url or saved['public_key'] != os.environ.get('LANGFUSE_PUBLIC_KEY'):
        raise ValueError('Historical prompt bindings belong to another target')
    client = get_langfuse(cfg)
    try:
        for key, expected in prompts().items():
            bound = saved['bindings'][key]
            if bound['name'] != expected['name'] or type(bound['version']) is not int or bound['version'] < 1:
                raise ValueError('Historical prompt binding differs from frozen corpus')
            prompt = client.get_prompt(bound['name'], version=bound['version'], cache_ttl_seconds=0,
                                       max_retries=0, fetch_timeout_seconds=15)
            if prompt.prompt != expected['text']:
                raise ValueError('Historical prompt version no longer matches the frozen corpus')
        return saved['bindings']
    finally:
        client.flush()
        client.shutdown()
