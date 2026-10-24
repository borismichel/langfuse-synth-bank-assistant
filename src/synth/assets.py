"""Bounded, non-model setup of owned banking assets through the supported SDK."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

from langfuse.api import NotFoundError
from langfuse_synth_core.lfclient import get_langfuse
from langfuse_synth_core.seed.ingest import assert_demo_project
from .receipt import RunReceipt

OWNER = 'northwind-depot'
REQUEST = {'max_retries': 0, 'timeout_in_seconds': 15}


def corpus():
    return json.loads(Path(__file__).with_name('assets.json').read_text())


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def state_path(name):
    return Path(RunReceipt.state_dir()) / name


def save(name, data):
    path = state_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, sort_keys=True))
    tmp.replace(path)


@contextmanager
def setup_lock():
    path = state_path('asset-setup.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Asset setup is already running for this deployment') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def owned_target(cfg, saved):
    if (saved.get('base_url') != cfg.target.base_url or
            saved.get('public_key') != os.environ.get('LANGFUSE_PUBLIC_KEY')):
        raise ValueError('Asset receipt belongs to another target')


def definition_matches(row, definition, description):
    actual = row.model_dump()
    return (not row.is_archived and row.description == description and
            all(actual.get(key) == value for key, value in definition.items()))


def definitions(client):
    rows = []
    for page in range(1, 11):
        result = client.api.score_configs.get(page=page, limit=100, request_options=REQUEST)
        rows.extend(result.data)
        if result.meta.total_pages > 10:
            raise ValueError('Score definition scan exceeds the bounded 1000-definition limit')
        if page >= result.meta.total_pages:
            return rows
    raise ValueError('Incomplete score definition scan')


def prompt_url(project_url, name, version):
    return project_url + '/prompts/prompt-detail?' + urlencode({'promptName': name, 'version': version})


def run_setup_assets(cfg):
    from .setup import run_setup_prompts
    run_setup_prompts(cfg)
    with setup_lock():
        client = get_langfuse(cfg)
        try:
            project_id, _ = assert_demo_project(cfg.target.base_url, cfg.target.project_hint)
            project_url = cfg.target.base_url + '/project/' + quote(project_id, safe='')
            source = corpus()
            receipt = dict(schema_version=1, base_url=cfg.target.base_url,
                public_key=os.environ['LANGFUSE_PUBLIC_KEY'], project_id=project_id,
                corpus_hash=fingerprint(source), prompts={}, score_definitions={})
            for key, text in source['prompts'].items():
                name = f'northwind-{key}-{fingerprint(text)[:12]}'
                try:
                    prompt = client.get_prompt(name, label='latest', cache_ttl_seconds=0,
                                               max_retries=0, fetch_timeout_seconds=15)
                except NotFoundError:
                    prompt = client.create_prompt(name=name, prompt=text, type='text',
                        tags=[OWNER, 'rubric-fixture'], commit_message='Frozen banking rubric; no evaluator activation')
                if prompt.prompt != text or OWNER not in prompt.tags:
                    raise ValueError(f'Prompt ownership/content conflict: {name}; preserved without modification')
                receipt['prompts'][key] = dict(name=name, version=prompt.version,
                    content_hash=fingerprint(text), url=prompt_url(project_url, name, prompt.version))
            rows = definitions(client)
            for key, definition in source['score_definitions'].items():
                description = f'{OWNER}; schema1; content={fingerprint(definition)}; constructed/history or explicit future review dimension'
                matches = [row for row in rows if row.name == definition['name']]
                if len(matches) > 1:
                    raise ValueError(f'Ambiguous score definition {key}; no resources overwritten')
                if matches:
                    row = matches[0]
                else:
                    row = client.api.score_configs.create(**definition, description=description, request_options=REQUEST)
                if not definition_matches(row, definition, description):
                    raise ValueError(f'Score definition ownership/content conflict: {key}; preserved without modification')
                receipt['score_definitions'][key] = {'id': row.id, 'definition': definition, 'description': description}
            save('asset-identities.json', receipt)
            print('Reusable rubric prompts and score definitions prepared; no evaluators or model calls enabled', flush=True)
            return 0
        finally:
            client.flush()
            client.shutdown()
