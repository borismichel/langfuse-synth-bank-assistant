"""One bounded, loopback-only Jaeger child owned by this Companion process."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import tempfile
from pathlib import Path

import httpx


class JaegerService:
    def __init__(self, *, enabled: bool, binary='/usr/local/bin/jaeger'):
        self.enabled, self.binary = enabled, binary
        self.process = None
        self.error = None
        self.started = False
        self.monitor = None
        self.directory = None
        self.query_url = ''
        self.otlp_url = ''
        self.stopping = False

    @property
    def ready(self):
        return self.started and self.process is not None and self.process.returncode is None

    async def start(self):
        if not self.enabled:
            return
        if not shutil.which(self.binary):
            self.error = 'APMDependencyUnavailable'
            return
        ports = []
        for _ in range(3):
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                ports.append(listener.getsockname()[1])
        query, receiver, query_grpc = ports
        self.query_url = f'http://127.0.0.1:{query}'
        self.otlp_url = f'http://127.0.0.1:{receiver}/v1/traces'
        self.directory = tempfile.TemporaryDirectory(prefix='northwind-apm-')
        path = Path(self.directory.name) / 'jaeger.json'
        path.write_text(json.dumps({
            'service': {
                'extensions': ['jaeger_storage', 'jaeger_query'],
                'pipelines': {'traces': {'receivers': ['otlp'],
                    'processors': ['memory_limiter', 'batch'], 'exporters': ['jaeger_storage_exporter']}},
                'telemetry': {'logs': {'level': 'error'}, 'metrics': {'level': 'none'},
                              'traces': {'processors': []}},
            },
            'extensions': {
                'jaeger_storage': {'backends': {'local': {'memory': {'max_traces': 1000}}}},
                'jaeger_query': {'storage': {'traces': 'local'}, 'base_path': '/apm',
                    'http': {'endpoint': f'127.0.0.1:{query}'},
                    'grpc': {'endpoint': f'127.0.0.1:{query_grpc}'},
                    'ui': {'log_access': False}},
            },
            'receivers': {'otlp': {'protocols': {'http': {'endpoint': f'127.0.0.1:{receiver}'}}}},
            'processors': {'memory_limiter': {'check_interval': '1s', 'limit_mib': 256,
                                              'spike_limit_mib': 64},
                           'batch': {'timeout': '100ms', 'send_batch_size': 128, 'send_batch_max_size': 256}},
            'exporters': {'jaeger_storage_exporter': {'trace_storage': 'local'}},
        }))
        # No project, provider, portal or OTel exporter credentials enter Jaeger.
        env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'LANG'}}
        env.update(GOMEMLIMIT='256MiB', GOMAXPROCS='2', OTEL_TRACES_SAMPLER='always_off')
        try:
            self.process = await asyncio.create_subprocess_exec(self.binary, '--config', str(path), env=env)
            async with asyncio.timeout(15), httpx.AsyncClient(trust_env=False, timeout=.5) as client:
                while self.process.returncode is None:
                    try:
                        response = await client.get(self.query_url + '/apm/api/v3/services')
                        if response.status_code == 200:
                            self.started = True
                            self.monitor = asyncio.create_task(self.watch())
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.1)
            self.error = 'APMProcessExited'
        except TimeoutError:
            self.error = 'APMStartupTimeout'
        except OSError:
            self.error = 'APMDependencyUnavailable'
        await self.stop()

    async def watch(self):
        await self.process.wait()
        self.started = False
        if not self.stopping:
            self.error = 'APMProcessExited'

    async def stop(self):
        self.stopping = True
        self.started = False
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.monitor:
            await self.monitor
        if self.directory:
            self.directory.cleanup()
