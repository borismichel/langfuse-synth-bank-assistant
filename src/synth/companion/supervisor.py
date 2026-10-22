"""Own exactly one private MCP child for this Companion lifetime."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class BankingService:
    def __init__(self, adapter):
        self.adapter = adapter
        self.secret = secrets.token_hex(32)
        self.instance = os.environ.get('PORTAL_LIVE_INSTANCE_ID') or 'local-' + secrets.token_hex(12)
        self.epoch = secrets.token_hex(16)
        self.process = None
        self.url = ''
        self.started = False
        self.error = None
        self.monitor = None
        self.stopping = False

    @property
    def ready(self):
        return self.started and self.process is not None and self.process.returncode is None

    async def start(self):
        if not os.environ.get('LANGFUSE_PUBLIC_KEY') or not os.environ.get('LANGFUSE_SECRET_KEY'):
            self.error = 'MissingProjectCredentials'
            return
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}/mcp'
        allowed = {'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'PYTHONPATH',
                   'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                   'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                   'LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_TRACING_ENVIRONMENT'}
        env = {k: v for k, v in os.environ.items() if k in allowed}
        env.update(LANGFUSE_BASE_URL=self.adapter.base_url,
                   NORTHWIND_MCP_PORT=str(port), NORTHWIND_BINDING_SECRET=self.secret,
                   PORTAL_LIVE_INSTANCE_ID=self.instance, NORTHWIND_PROCESS_EPOCH=self.epoch,
                   PYTHONUNBUFFERED='1')
        # Child stdout/stderr inherit the container log stream; no shell/detached process.
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'synth.companion.mcp_runtime', env=env)
        try:
            async with asyncio.timeout(15):
                while self.process.returncode is None:
                    try:
                        async with (streamablehttp_client(self.url) as (read, write, _),
                                    ClientSession(read, write) as session):
                            await session.initialize()
                            await session.list_tools()
                        self.started = True
                        self.monitor = asyncio.create_task(self.watch())
                        print('Banking service ready', flush=True)
                        return
                    except Exception:  # noqa: BLE001 — startup remains live on any child failure.
                        await asyncio.sleep(.1)
            self.error = 'BankingProcessExited'
        except TimeoutError:
            self.error = 'BankingStartupTimeout'
        print('Banking service unavailable: ' + str(self.error), flush=True)
        await self.stop()

    async def stop(self):
        self.stopping = True
        self.started = False
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                self.process.kill()  # Only the exact child we created.
                await self.process.wait()
        if self.monitor:
            await self.monitor
        print('Banking service stopped', flush=True)

    async def reset(self, state):
        from northwind.binding import sign
        binding = {'instance': self.instance, 'epoch': self.epoch, 'operation': 'reset',
                   'scenario_id': state['scenario_id'], 'revision': state['scenario']['revision'],
                   'session_id': state['session_id'], 'customer_id': state['customer_id']}
        async with (streamablehttp_client(self.url) as (read, write, _),
                    ClientSession(read, write) as session):
            await session.initialize()
            result = await session.call_tool('reset_scenario', {'customer_id': state['customer_id']},
                                            meta={'northwind_binding': sign(binding, self.secret)})
            data = result.structuredContent or {}
            if not data:
                data = json.loads(next(c.text for c in result.content if c.type == 'text'))
            data = data.get('result', data)
            if data.get('status') != 'reset':
                raise RuntimeError('Banking reset did not complete')
            return data['revision']

    async def watch(self):
        code = await self.process.wait()
        self.started = False
        if not self.stopping:
            self.error = 'BankingProcessExited'
            print(f'Banking service exited with status {code}; state is unavailable', flush=True)
