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
import re
import sys
import tempfile

import httpx


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class _WorkflowDiagnostics:
    """Drain every child pipe, retaining at most one bounded line per reader.

    Forwarding has a single service-lifetime budget, independent of child count.
    Oversized lines are omitted whole so splitting cannot disclose a secret.
    Fixed lifecycle summaries have their own small, finite number of call sites.
    """
    def __init__(self, token):
        self.secrets = {token} if token else set()
        self.remaining_bytes = 32768
        self.remaining_lines = 128
        self.suppressed = False

    def protect(self, *values):
        self.secrets.update(value for value in values if value)

    def _clean(self, value):
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '[redacted]')
        # An assignment may contain a quoted value, spaces, or a JSON object.
        # Omit its entire suffix rather than guessing where that value ends.
        value = re.sub(r'(?i)([\"\']?(?:password|passwd|token|secret|api[_-]?key|'
                       r'authorization|cookie|encryption[_-]?key|n8n-auth)[\"\']?\s*[:=]).*',
                       r'\1 [redacted]', value)
        value = re.sub(r'(?i)\b(?:bearer|basic)\s+\S+', '[redacted]', value)
        value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
        return ''.join(char for char in value if char.isprintable() or char == '\t')

    def emit(self, stage, stream, value):
        if self.suppressed:
            return
        line = f'northwind-n8n stage={stage} stream={stream} {self._clean(value)}\n'
        size = len(line.encode())
        if self.remaining_lines <= 0 or size > self.remaining_bytes:
            if not self.suppressed:
                print('northwind-n8n diagnostic omitted: forwarding budget exhausted', file=sys.stderr, flush=True)
                self.suppressed = True
            return
        self.remaining_lines -= 1
        self.remaining_bytes -= size
        sys.stderr.write(line)
        sys.stderr.flush()

    def exited(self, stage, code):
        # Fixed labels and an integer only; do not format child exceptions/argv.
        print(f'northwind-n8n stage={stage} exit={code}', file=sys.stderr, flush=True)

    async def drain(self, pipe, stage, stream):
        pending = bytearray()
        oversized = False
        while chunk := await pipe.read(4096):
            for part in chunk.splitlines(keepends=True):
                complete = part.endswith(b'\n') or part.endswith(b'\r')
                if not oversized:
                    if len(pending) + len(part) > 2048:
                        pending.clear()
                        oversized = True
                    else:
                        pending.extend(part)
                if complete:
                    self.emit(stage, stream, '[oversized diagnostic omitted]' if oversized else
                              pending.decode('utf-8', errors='replace').rstrip('\r\n'))
                    pending.clear()
                    oversized = False
        if oversized or pending:
            self.emit(stage, stream, '[oversized diagnostic omitted]' if oversized else
                      pending.decode('utf-8', errors='replace'))

    def readers(self, child, stage):
        return [asyncio.create_task(self.drain(pipe, stage, stream))
                for pipe, stream in ((child.stdout, 'stdout'), (child.stderr, 'stderr'))]

    async def finish(self, child, readers):
        try:
            await asyncio.wait_for(asyncio.gather(*readers), 2)
        except TimeoutError:
            # A descendant may still hold the pipe open after its parent exits.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # asyncio exposes no public Process.close; close our owned pipe
            # transports after cancelling readers so no descriptor survives.
            child._transport.close()
            await asyncio.gather(*readers, return_exceptions=True)


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
        self.readers = []
        self.error = None
        self.cookie = ''
        self.url = ''
        self.env = {}
        self.diagnostics = _WorkflowDiagnostics(token)

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
        self.diagnostics.protect(self.env['N8N_ENCRYPTION_KEY'], self.env['N8N_RUNNERS_AUTH_TOKEN'])
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
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
            self.readers = self.diagnostics.readers(self.process, 'runtime')
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
                        self.error = 'WorkflowProcessExited'
                        raise RuntimeError(self.error)
                email = 'presenter-' + secrets.token_hex(8) + '@example.invalid'
                password = secrets.token_urlsafe(32) + 'Aa1!'
                self.diagnostics.protect(email, password)
                response = await client.post(self.url + '/rest/owner/setup', json={
                    'email': email, 'firstName': 'Depot', 'lastName': 'Presenter', 'password': password})
                response.raise_for_status()
                self.cookie = '; '.join(k + '=' + v for k, v in response.cookies.items())
                self.diagnostics.protect(self.cookie, *response.cookies.values())
                if not self.cookie:
                    raise RuntimeError('WorkflowAuthenticationUnavailable')
            self.ready = True
            self.monitor = asyncio.create_task(self._watch())
        except Exception as exc:
            self.error = self.error or type(exc).__name__
            await self.stop()

    async def _command(self, *args):
        stage = args[0]
        child = await asyncio.create_subprocess_exec('n8n', *args, env=self.env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        readers = self.diagnostics.readers(child, stage)
        try:
            code = await asyncio.wait_for(child.wait(), 60)
            if code:
                self.error = 'WorkflowImportFailed' if stage.startswith('import:') else 'WorkflowPublishFailed'
                raise RuntimeError(self.error)
        except TimeoutError:
            self.error = 'WorkflowCommandTimeout'
            raise
        finally:
            if child.returncode is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await child.wait()
            await self.diagnostics.finish(child, readers)
            self.diagnostics.exited(stage, child.returncode)

    async def _watch(self):
        # Process.wait also waits for pipe EOF. A descendant holding a pipe
        # must not keep a dead runtime ready; readers have a separate bound.
        while self.process.returncode is None:
            await asyncio.sleep(.1)
        if self.ready:
            self.ready = False
            self.error = 'WorkflowProcessExited'
        await self._finish_runtime()

    async def _finish_runtime(self):
        if self.readers:
            readers, self.readers = self.readers, []
            await self.diagnostics.finish(self.process, readers)
            self.diagnostics.exited('runtime', self.process.returncode)

    async def stop(self):
        self.ready = False
        if self.process and (self.process.returncode is None or self.readers):
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 15)
            except TimeoutError:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()
        if self.monitor:
            await self.monitor
        else:
            await self._finish_runtime()
        if self.directory:
            self.directory.cleanup()
        self.cookie = ''
