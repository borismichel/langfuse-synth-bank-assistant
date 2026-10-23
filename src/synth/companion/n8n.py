"""Per-instance, opt-in workflow runtime. No runtime package installation."""
from __future__ import annotations

import asyncio
import os
import json
from pathlib import Path
import secrets
import shutil
import signal
import socket
import tempfile

import httpx


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class N8nService:
    def __init__(self, *, enabled, base_path, bridge_url, token, public_origin=None):
        self.enabled = enabled
        self.base_path = base_path.rstrip('/')
        self.bridge_url = bridge_url
        self.token = token
        self.public_origin = public_origin or os.environ.get('PORTAL_LIVE_PUBLIC_ORIGIN', '')
        self.ready = False
        self.process = None
        self.directory = None
        self.monitor = None
        self.error = None
        self.cookie = ''
        self.url = ''
        self.env = {}

    async def start(self):
        if not self.enabled:
            return
        if not shutil.which('n8n'):
            self.error = 'WorkflowRuntimeUnavailable'
            return
        if not self.public_origin:
            self.error = 'WorkflowPublicOriginMissing'
            return
        self.directory = tempfile.TemporaryDirectory(prefix='northwind-n8n-')
        port, broker = _port(), _port()
        self.url = f'http://127.0.0.1:{port}'
        # No inherited provider/project/portal secrets, execution environment or hooks.
        self.env = {k: v for k, v in os.environ.items() if k in {'PATH', 'LANG'}}
        self.env.update({
            'HOME': self.directory.name, 'TMPDIR': self.directory.name,
            'N8N_USER_FOLDER': self.directory.name,
            'N8N_LISTEN_ADDRESS': '127.0.0.1', 'N8N_PORT': str(port),
            'N8N_HOST': '127.0.0.1', 'N8N_PROTOCOL': 'http',
            'N8N_PATH': self.base_path + '/',
            'N8N_EDITOR_BASE_URL': self.public_origin + self.base_path + '/',
            'N8N_WEBHOOK_URL': self.public_origin + self.base_path + '/',
            'N8N_ENCRYPTION_KEY': secrets.token_hex(32),
            'N8N_DIAGNOSTICS_ENABLED': 'false', 'N8N_VERSION_NOTIFICATIONS_ENABLED': 'false',
            'N8N_TEMPLATES_ENABLED': 'false', 'N8N_COMMUNITY_PACKAGES_ENABLED': 'false',
            'N8N_DIAGNOSTICS_CONFIG_FRONTEND': '', 'N8N_DIAGNOSTICS_CONFIG_BACKEND': '',
            'EXTERNAL_FRONTEND_HOOKS_URLS': '', 'N8N_PERSONALIZATION_ENABLED': 'false',
            'N8N_BLOCK_ENV_ACCESS_IN_NODE': 'true', 'N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES': 'true',
            'N8N_PUBLIC_API_DISABLED': 'true', 'N8N_PUSH_BACKEND': 'sse',
            'N8N_RUNNERS_MODE': 'external', 'N8N_RUNNERS_AUTH_TOKEN': secrets.token_hex(32),
            'N8N_RUNNERS_BROKER_LISTEN_ADDRESS': '127.0.0.1', 'N8N_RUNNERS_BROKER_PORT': str(broker),
            'NODES_INCLUDE': '["n8n-nodes-base.manualTrigger","n8n-nodes-base.set","n8n-nodes-base.webhook","n8n-nodes-base.httpRequest"]',
            'N8N_LOG_LEVEL': 'error', 'N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS': 'true',
            'EXECUTIONS_TIMEOUT': '120', 'EXECUTIONS_TIMEOUT_MAX': '120',
            'N8N_CONCURRENCY_PRODUCTION_LIMIT': '1', 'EXECUTIONS_DATA_PRUNE': 'true',
            'EXECUTIONS_DATA_MAX_AGE': '1', 'EXECUTIONS_DATA_PRUNE_MAX_COUNT': '50',
            'N8N_GRACEFUL_SHUTDOWN_TIMEOUT': '10', 'NODE_OPTIONS': '--max-old-space-size=768',
        })
        try:
            from .complaint_workflow import credentials, workflow, WORKFLOW_ID
            for command, name, content in (
                ('import:credentials', 'credentials.json', credentials(self.token)),
                ('import:workflow', 'workflow.json', [workflow(self.bridge_url)]),
            ):
                path = Path(self.directory.name) / name
                path.write_text(json.dumps(content))
                path.chmod(0o600)
                try:
                    await self._command(command, '--input=' + str(path))
                finally:
                    path.unlink(missing_ok=True)
            await self._command('publish:workflow', '--id=' + WORKFLOW_ID)
            self.env.update({
                'N8N_OTEL_ENABLED': 'true',
                'N8N_OTEL_EXPORTER_OTLP_ENDPOINT': self.bridge_url,
                'N8N_OTEL_EXPORTER_OTLP_TRACING_PATH': '/otel',
                'N8N_OTEL_EXPORTER_OTLP_HEADERS': 'X-Northwind-Workflow=' + self.token,
                'N8N_OTEL_TRACES_PRODUCTION_ONLY': 'false',
                'N8N_OTEL_TRACES_INCLUDE_NODE_SPANS': 'true',
                'N8N_OTEL_TRACES_INJECT_OUTBOUND': 'true',
                'N8N_OTEL_EXPORTER_SERVICE_NAME': 'northwind-n8n',
            })
            self.process = await asyncio.create_subprocess_exec('n8n', 'start', env=self.env,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                async with asyncio.timeout(90):
                    while self.process.returncode is None:
                        try:
                            response = await client.get(self.url + '/healthz/readiness')
                            if response.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.25)
                    else:
                        raise RuntimeError('WorkflowProcessExited')
                response = await client.post(self.url + '/rest/owner/setup', json={
                    'email': 'presenter-' + secrets.token_hex(8) + '@example.invalid',
                    'firstName': 'Depot', 'lastName': 'Presenter',
                    'password': secrets.token_urlsafe(32) + 'Aa1!'})
                response.raise_for_status()
                self.cookie = '; '.join(k + '=' + v for k, v in response.cookies.items())
                if not self.cookie:
                    raise RuntimeError('WorkflowAuthenticationUnavailable')
            self.ready = True
            self.monitor = asyncio.create_task(self._watch())
        except Exception as exc:
            self.error = type(exc).__name__
            await self.stop()

    async def _command(self, *args):
        child = await asyncio.create_subprocess_exec('n8n', *args, env=self.env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
        try:
            code = await asyncio.wait_for(child.wait(), 60)
            if code:
                raise RuntimeError('WorkflowImportFailed')
        finally:
            if child.returncode is None:
                os.killpg(child.pid, signal.SIGKILL)
                await child.wait()

    async def _watch(self):
        await self.process.wait()
        if self.ready:
            self.ready = False
            self.error = 'WorkflowProcessExited'

    async def stop(self):
        self.ready = False
        if self.process and self.process.returncode is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 15)
            except TimeoutError:
                os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        if self.monitor:
            await self.monitor
        if self.directory:
            self.directory.cleanup()
        self.cookie = ''
