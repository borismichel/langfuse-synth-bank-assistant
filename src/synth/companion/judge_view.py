"""Presenter policy inspection, using text nodes for target-provided evidence."""
import json
from html import escape


def judge_page(provider,prefix):
    return PAGE.replace('__PREFIX__',escape(prefix,quote=True)).replace('__PROVIDER__',escape(provider)).replace(
        '__BASE_JSON__',json.dumps(prefix+'/manage/judges').replace('<','\\u003c'))


PAGE='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Policy judges</title><link rel="stylesheet" href="__PREFIX__/static/app.css">
</head><body class="langfuse-theme bank-presenter"><main>
<nav><a href="__PREFIX__/">Customer assistant</a> · <a href="__PREFIX__/history">Historical evidence</a></nav>
<h1 class="lf-heading">Policy judges</h1><p>Managed checks for investment advice, credentials, selling, promises and deposit insurance. These fictional banking policies are demonstration criteria.</p>
<p id="notice" class="notice" role="status">Reading native judge status…</p>
<section class="lf-frame corner-box-corners"><h2>Configuration</h2><p>Selected provider: <strong>__PROVIDER__</strong>. The judge model is separate from the chat model.</p>
<form id="activate"><div class="fields"><label>Judge model<input name="model" required maxlength="120" autocomplete="off" placeholder="Model available on the selected provider"></label>
<label>Sampling fraction<input name="sampling" type="number" min="0.01" max="1" step="0.01" value="1" required></label></div>
<p class="small">Send a live banking answer before activation to verify its judge inputs. After activation, send new answers; earlier turns are not backfilled. Model access and billing are verified by actual evaluation results.</p>
<label class="ack"><input name="cost" type="checkbox" required>I understand that each sampled answer can trigger five paid evaluations and remote rules continue after the Companion stops.</label>
<button class="primary lf-button lf-button__label">Activate policy judges</button></form><button class="lf-button lf-button__label" id="pause">Pause policy judges</button><button class="lf-button lf-button__label" id="refresh">Refresh status</button>
<p class="small">Pause before ending the deployment. Already queued work may finish. A paused evaluator may require reactivation in Langfuse after its credential or billing issue is resolved.</p></section>
<section class="lf-frame corner-box-corners"><h2>Native status</h2><dl id="summary" class="summary"></dl><p id="checked" class="small"></p><p id="native"></p>
<div class="table-scroll"><table><thead><tr><th>Policy</th><th>Evaluator status</th><th>Version</th><th>Native evaluator</th></tr></thead><tbody id="policies"></tbody></table></div></section>
<section class="lf-frame corner-box-corners"><h2>Reconciliation</h2><p id="reconciliation">No reconciliation yet.</p><button class="lf-button lf-button__label" id="reconcile">Reconcile results</button>
<p class="small">Reads native results and updates owned derived scores without a model call. Automatic reconciliation runs every 30 seconds while this Companion is running. A rejected or uncertain write requires native inspection before retry. Remote judges may continue after the Companion stops.</p></section>
<section class="lf-frame corner-box-corners"><h2>Delivered answers</h2><p id="coverage">No readback yet.</p><p id="scope" class="small"></p><p id="controls" class="small"></p><div id="answers"></div></section>
</main><script>
const base=__BASE_JSON__;const byId=id=>document.getElementById(id);let polling=false;
function element(tag,text,parent,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;if(parent)parent.append(n);return n;}
function link(url,label,parent){const a=element('a',label,parent);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a;}
function datum(label,value){const d=element('div',undefined,byId('summary'));element('dt',label,d);element('dd',value,d);}
function render(v){
 byId('notice').textContent=v.readback_error||v.operation?.error||(v.operation?.status==='running'?'Preparing policy judges…':v.enabled===true?'Rule enabled. Refresh after a new answer to inspect managed results.':v.enabled===false?(v.rule_id?'Rule disabled. No new evaluations are requested by this rule.':'Policy judges are not configured.'):'Rule state has not been confirmed.');
 byId('summary').replaceChildren();datum('Rule',v.enabled===true?'Enabled':v.enabled===false?(v.rule_id?'Disabled':'Not configured'):'Unconfirmed');datum('Provider credential',v.credential_available?'Available':'Missing');datum('Judge model',v.identity?.selection.model||'Not selected');datum('Sampling',v.identity?String(v.identity.selection.sampling):'Not selected');
 byId('checked').textContent=v.checked_at?'Read back '+new Date(v.checked_at).toLocaleString()+'. Results are a snapshot.':'Native results have not been read back.';
 byId('native').replaceChildren();if(v.rules_url)link(v.rules_url,'Open native evaluation rules',byId('native'));
 byId('policies').replaceChildren();const labels={P1:'Investment advice',P2:'Credentials',P3:'Selling',P4:'Promises',P5:'Deposit insurance'};
 for(const [id,title] of Object.entries(labels)){const r=(v.evaluators||[]).find(x=>x.policy===id),tr=element('tr',undefined,byId('policies'));element('td',id+' · '+title,tr);element('td',r?r.status+(r.paused_reason?' · '+r.paused_reason:''):'Not configured',tr);element('td',r?String(r.version):'—',tr);const td=element('td',undefined,tr);if(r)link(r.url,'Inspect evaluator',td);}
 if(v.identity){const form=byId('activate');form.elements.model.value=v.identity.selection.model;form.elements.sampling.value=v.identity.selection.sampling;form.elements.model.readOnly=true;form.elements.sampling.readOnly=true;}
 const c=v.coverage;byId('coverage').textContent=c?`${c.fully_scored} of ${c.eligible} eligible answers have all five managed results; ${c.scored_policy_results} of ${c.possible_policy_results} possible policy results are present.`:'Refresh status to read managed results.';
 byId('scope').textContent=(v.evidence?.scope||'No eligible evidence has been inspected.')+' Missing results can be pending, unsampled or from before activation. They are not passes. Unknown verdicts remain unknown.';
 const n=v.evidence?.negative;byId('controls').textContent=n?`Excluded blocked controls observed: ${n.blocked}. Historical sample observed: ${n.history}.`:'Exclusion controls have not been inspected.';
 const sync=v.reconciliation;byId('reconciliation').textContent=sync?`${sync.lifecycle||'Not started'} · ${sync.status}${sync.pending_scores?` · ${sync.pending_scores} scores awaiting native readback`:''}${sync.error?' · '+sync.error:''}`:'No reconciliation yet.';
 byId('reconcile').textContent=sync?.status==='action_required'||sync?.status==='error'?'Retry reconciliation':'Reconcile results';
 if(c)byId('coverage').textContent+=` Answers with results: ${c.scored}; unscored: ${c.unscored}; excluded blocked: ${c.blocked}; awaiting results: ${c.pending}; unknown, invalid, ambiguous or paused: ${c.error}. Counts overlap where an answer has both known and unresolved policies.`;
 byId('answers').replaceChildren();for(const a of v.evidence?.eligible||[]){const box=element('article',undefined,byId('answers'),'answer');link(a.url,'Open answer in Langfuse',box);element('p',a.query,box);const r=(v.results||[]).find(x=>x.observation_id===a.observation_id&&x.trace_id===a.trace_id),badges=element('div',undefined,box,'verdicts');for(const id of Object.keys(labels)){const p=r?.policies.find(x=>x.policy===id);element('span',id+': '+(p?(p.state==='scored'?p.value:({not_configured:'Not configured',pending:'Pending / unsampled',paused:'Paused',ambiguous:'Ambiguous'}[p.state]||p.state)):'Not read'),badges,'verdict');}
 if(r?.headline){element('p',`${r.headline.status} · ${Math.round(r.headline.coverage*100)}% known policy coverage · ${r.headline.score===null?'No numeric headline':'Headline '+r.headline.score}`,box);}
 const d=element('details',undefined,box);element('summary','Evidence supplied to judges',d);element('h3','Customer query',d);element('pre',a.query,d);element('h3','Delivered answer',d);element('pre',a.generation,d);element('h3','Policy and tool evidence',d);element('pre',a.context,d);for(const p of r?.policies||[]){if(p.reasoning){const reason=element('details',undefined,box);element('summary',p.policy+' reasoning',reason);element('p',p.reasoning,reason);}}
 }
}
async function call(path,body){const r=await fetch(base+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const v=await r.json();if(!r.ok)throw Error(v.detail||'Request failed');render(v);return v;}
async function refresh(remote=true){try{const v=await call('/status'+(remote?'?refresh=true':''));if(v.operation?.status==='running'){polling=true;setTimeout(()=>refresh(false),1500);}else if(polling){polling=false;await call('/status?refresh=true');}}catch(e){byId('notice').textContent=e.message;}}
byId('activate').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{await call('/activate',{request_id:crypto.randomUUID(),model:f.get('model'),sampling:Number(f.get('sampling')),cost_acknowledged:f.get('cost')==='on'});refresh(false);}catch(x){byId('notice').textContent=x.message;}};
byId('reconcile').onclick=async()=>{try{await call('/reconcile',{request_id:crypto.randomUUID()});}catch(x){byId('notice').textContent=x.message;}};
byId('pause').onclick=async()=>{try{await call('/pause',{request_id:crypto.randomUUID()});refresh();}catch(x){byId('notice').textContent=x.message;}};byId('refresh').onclick=()=>refresh();refresh();setInterval(()=>{if(!polling&&!document.querySelector('details[open]'))refresh(false);},5000);
</script></body></html>'''
