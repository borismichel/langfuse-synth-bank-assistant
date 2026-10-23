"""Read-only governance discovery; native user authorization is a separate boundary."""
from __future__ import annotations

from html import escape
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from langfuse_synth_core.companion.access import route_access, validate_protected_paths
from langfuse_synth_core.live import paths

CAPABILITIES = (
    ('rbac', 'User roles and permissions', 'administration/rbac', '',
     'Project API credentials do not identify the signed-in user or their effective project role. Inspect the current account and project membership in Langfuse.'),
    ('protected_labels', 'Protected prompt labels', 'prompt-management/features/prompt-version-control', '/settings/protected-prompt-labels',
     'Native enforcement requires the target entitlement, a configured protected label, and separately authorized administrator and restricted-user accounts. None is established by project API access.'),
    ('audit', 'Audit logs', 'administration/audit-logs', '/settings/audit-logs',
     'Audit availability requires the target Enterprise entitlement and the current user’s auditLogs:read permission. Project API access does not establish either.'),
    ('retention', 'Event data retention', 'administration/data-retention', '/settings',
     'Read the current project retention setting. Permission to change it and actual deletion enforcement require separate native qualification.'),
)


def initial_status():
    return dict(checked_at=None, target=None, credential_scope='project-api-key',
        connection=dict(status='disabled', reason='Target inspection is disabled in development preview.'),
        qualification=dict(status='disabled', reason='Automatic user-role and privileged governance qualification is disabled. Use the native interfaces with explicitly authorized accounts.'),
        capabilities=[dict(id=key, title=title, status='unavailable', reason=reason,
                           documentation_url='https://langfuse.com/docs/'+doc, native_url=None)
                      for key, title, doc, _, reason in CAPABILITIES])


def inspect_target(adapter):
    view = initial_status()
    view['checked_at'] = datetime.now(timezone.utc).isoformat()
    try:
        api = adapter.langfuse().api
        # Stable public SDK calls, bounded once each; no organization/admin credential.
        options = {'max_retries': 0, 'timeout_in_seconds': 5}
        projects = api.projects.get(request_options=options).data
        if len(projects) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', projects[0].id):
            raise ValueError('Project-scoped response required')
        project = projects[0]
        health = api.health.health(request_options=options)
        if health.status != 'OK' or not re.fullmatch(r'[A-Za-z0-9.+_-]{1,80}', health.version):
            raise ValueError('Healthy versioned target required')
        project_url = adapter.base_url + '/project/' + quote(project.id, safe='')
        view['target'] = dict(id=project.id, name=project.name[:200], version=health.version, url=project_url)
        view['connection'] = dict(status='available', reason='Project API access and target health were read successfully. This does not establish native user permissions or Enterprise entitlements.')
        for item, (_, _, _, native_path, _) in zip(view['capabilities'], CAPABILITIES):
            item['native_url'] = project_url + native_path
        retention = next(item for item in view['capabilities'] if item['id']=='retention')
        if health.version != '4.68.0':
            retention['reason'] = 'Retention interpretation is qualified for Langfuse 4.68.0. Inspect this target’s setting and API contract before claiming a retention state.'
        elif project.retention_days is None or project.retention_days == 0:
            retention.update(status='disabled', days=0, evidence='/api/public/projects: retentionDays',
                reason='The project has no automatic event deletion configured. This does not establish a data-access window or permission to change retention.')
        elif type(project.retention_days) is int and project.retention_days >= 3:
            retention.update(status='available', days=project.retention_days, evidence='/api/public/projects: retentionDays',
                reason=f'Event retention is configured for {project.retention_days} days. Configuration readback does not prove deletion enforcement or permission to change it. Saved dataset items and audit logs are excluded; source traces can expire.')
        else:
            retention.update(status='failed', reason='The target returned an invalid retention setting. Inspect native project settings before making a retention claim.')
    except Exception as exc:
        code = getattr(exc, 'status_code', None)
        reason = (f'Target readback failed (HTTP {code}). Check this project’s API access and target availability.'
                  if isinstance(code, int) else 'Target readback failed ('+type(exc).__name__+'). Check project-scoped credentials, target health and the supported API response.')
        # Neither arbitrary server error text nor credentials become browser evidence.
        view['connection'] = dict(status='failed', reason=reason)
    return view


def page(view):
    prefix = escape(paths.base_path(), quote=True)
    def link(url, title):
        return '<a target="_blank" rel="noopener noreferrer" href="'+escape(url, quote=True)+'">'+escape(title)+'</a>'
    cards = ''
    for item in view['capabilities']:
        native = (' · '+link(item['native_url'], 'Inspect in Langfuse')) if item['native_url'] else ''
        cards += '<section><h2>'+escape(item['title'])+'</h2><p><strong>'+escape(item['status'].title())+'</strong> — '+escape(item['reason'])+'</p><p>'+link(item['documentation_url'], 'Documentation chapter')+native+'</p></section>'
    target = view['target']
    target_text = ('<p>Project: '+escape(target['name'])+' · '+escape(target['id'])+' · Langfuse '+escape(target['version'])+'</p>') if target else ''
    checked = '<p>Checked '+escape(view['checked_at'])+'</p>' if view['checked_at'] else ''
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Governance</title><link rel="stylesheet" href="'+prefix+'/static/app.css"><style>body{height:auto;overflow:auto}main{display:block;max-width:960px;margin:32px auto;padding:0 24px}h1{font-size:28px;margin:24px 0 12px}h2{font-size:20px;margin:24px 0 12px}p{margin:12px 0;line-height:1.6}section{border-top:1px solid #d6d8dc;margin:24px 0;padding-top:8px}a{color:#1760b0;text-decoration:underline}li{margin:12px 0}ul{padding-left:24px}</style></head><body><main><nav><a href="'+prefix+'/">Customer assistant</a></nav><h1>Governance</h1><p role="status">Target connection: '+escape(view['connection']['status'])+'. '+escape(view['connection']['reason'])+'</p>'+target_text+checked+'<p><a href="'+prefix+'/manage/governance">Refresh target status</a></p><p>Native qualification: '+escape(view['qualification']['status'])+'. '+escape(view['qualification']['reason'])+'</p>'+cards+'<section><h2>Protected-label qualification</h2><p>The Companion uses an ordinary project API key. Langfuse 4.68 permits these keys to move protected labels. A successful promotion does not prove native user-role enforcement. Depot presenter access and Langfuse user permissions are separate controls.</p><p>Native settings links identify inspection locations, not proof of availability or permission.</p><ul><li>Record the target project, installed version, account role, time and actual native outcome.</li><li>Use separately authorized administrator and restricted-user accounts to demonstrate allowed and denied protected-label changes. Do not change the sole owner’s role.</li><li>Inspect existing project audit events. Do not create artificial audit evidence or export unrelated organization history.</li><li>Read retention without shortening it. Saved dataset items and audit logs remain outside event retention; source traces can expire.</li></ul><p><a href="'+prefix+'/manage/releases">Prompt promotion receipts</a> report actual API outcomes. A target denial must not be relabelled as a verified user-role denial.</p></section></main></body></html>'


def mount_governance(app, adapter, *, preview=False):
    lock = asyncio.Lock()

    @app.get('/manage/governance/status')
    async def status():
        if preview:
            return initial_status()
        policy = validate_protected_paths(json.loads(os.environ.get('PORTAL_LIVE_PROTECTED_PATHS', '[]')))
        if route_access('/manage/governance', policy) != 'manage' or not os.environ.get('PORTAL_LIVE_PROOF'):
            raise HTTPException(503, 'Managed governance access is not configured')
        if lock.locked():
            raise HTTPException(409, 'Target inspection is already running')
        async with lock:
            return await asyncio.to_thread(inspect_target, adapter)

    @app.get('/manage/governance', response_class=HTMLResponse)
    async def governance():
        return page(await status())
