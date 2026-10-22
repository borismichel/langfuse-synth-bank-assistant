"""Read-only, prefix-aware historical evidence. No live credentials reach the page."""
import json
from html import escape
from langfuse_synth_core.live import paths
from ..assets import state_path, owned_target, corpus, fingerprint
from ..receipt import RunReceipt
from ..story import validate_anchors


def history_view(adapter, *, preview=False):
    empty = dict(verified=False, cases=[], datasets=[], prompts=[], coverage=None,
                 status='Development preview — no imported history or target connection')
    if preview:
        return empty
    try:
        history = RunReceipt.load()
        owned_target(adapter.cfg, vars(history))
        validate_anchors(history.history_anchors, history.history_run_id)
        snapshot = json.loads(state_path('history-evidence.json').read_text())
        assets = json.loads(state_path('asset-receipt.json').read_text())
        owned_target(adapter.cfg, snapshot)
        owned_target(adapter.cfg, assets)
        if (snapshot.get('schema_version') != 1 or not snapshot.get('verified') or
                snapshot.get('corpus_hash') != fingerprint(corpus()) or
                snapshot.get('corpus_hash') != assets.get('corpus_hash') or
                snapshot.get('history_run_id') != history.history_run_id or
                assets.get('history_run_id') != history.history_run_id):
            raise ValueError('Current history has not passed verification')
        return {key:value for key,value in snapshot.items() if key not in {'public_key','base_url'}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {**empty, 'status':'Current-run history unavailable — complete deployment verification'}


def history_page(adapter, *, preview=False):
    view = history_view(adapter, preview=preview)
    prefix = escape(paths.base_path(), quote=True)
    def link(url, label):
        return '<a target="_blank" rel="noopener noreferrer" href="'+escape(url,quote=True)+'">'+escape(str(label))+'</a>'
    details = ''
    if view['verified']:
        counts = ''.join(f'<div><dt>{escape(key.title())}</dt><dd>{value}</dd></div>' for key,value in view['coverage'].items())
        rows = ''.join('<tr><td>'+link(c['url'],c['case_id'])+'</td><td>'+escape(c['language'])+'</td><td>'+escape(c['status'])+'</td><td>'+escape(c['timestamp'])+'</td></tr>' for c in view['cases'])
        datasets = ''.join('<li>'+link(ds['url'],ds['cohort'])+f' — {len(ds["item_ids"])} items; snapshot '+escape(ds['snapshot_at'])+'</li>' for ds in view['datasets'])
        prompts = ''.join('<li>'+link(p['url'],p['key'])+' — version '+str(p['version'])+'</li>' for p in view['prompts'])
        native = '<p>'+link(view['native_views']['traces'], 'Open full-run time window in Langfuse')+'</p><p>'+str(view['dates']['trace_count'])+' imported roots from '+escape(view['dates']['first'])+' to '+escape(view['dates']['last'])+'. The time window may include unrelated records; case links identify exact current-run turns.</p>'
        details = native + '<p>'+escape(view['provenance'])+'</p><p>'+escape(view['scope'])+'</p><p>Verified '+escape(view['verified_at'])+'. This page shows a deployment verification snapshot; open the native links to inspect current records.</p><section><h2>Conduct coverage</h2><dl class="counts">'+counts+'</dl><p>'+escape(view['coverage_note'])+'</p></section><section><h2>Banking task checks</h2><p>'+str(view['quality']['scored'])+' scored of '+str(view['quality']['eligible'])+' eligible; '+str(view['quality']['passed'])+' passed and '+str(view['quality']['failed'])+' failed. Constructed historical outcomes.</p></section><section><h2>Fixed datasets</h2><ul>'+datasets+'</ul></section><details><summary>Prepared rubric prompt versions</summary><ul>'+prompts+'</ul></details><section><h2>Required turns</h2><div class="table-scroll"><table><thead><tr><th>Case</th><th>Language</th><th>Status</th><th>Recorded at</th></tr></thead><tbody>'+rows+'</tbody></table></div></section>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Banking history</title><link rel="stylesheet" href="'+prefix+'/static/app.css"><style>body{height:auto;overflow:auto}main{display:block;max-width:1100px;margin:32px auto;padding:0 24px}h1{font-size:28px;margin:24px 0 12px}h2{font-size:20px;margin:28px 0 12px}p{margin:12px 0;line-height:1.6}section,details{margin:24px 0}ul{padding-left:24px}li{margin:10px 0}a{color:#1760b0;text-decoration:underline}.counts{display:flex;flex-wrap:wrap;gap:24px;margin:20px 0}.counts dt{font-size:13px}.counts dd{font-size:26px;font-weight:700;margin:6px 0}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;border-bottom:1px solid #d6d8dc;padding:12px 10px;vertical-align:top}summary{cursor:pointer;font-weight:600}</style></head><body><main><nav><a href="'+prefix+'/">Customer assistant</a></nav><h1>Historical banking evidence</h1><p role="status">'+escape(view['status'])+'</p>'+details+'</main></body></html>'
