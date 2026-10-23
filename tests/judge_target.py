"""External stable 4.68 API fixture for delivered judge controls."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse, parse_qs


def matches(row, filters):
    for f in filters:
        value = row.get(f['column'])
        if f.get('key'): value=(value or {}).get(f['key'])
        if f['operator']=='=' and value!=f['value']: return False
        if f['operator']=='any of' and value not in f['value']: return False
        if f['operator']=='none of' and set(value or []) & set(f['value']): return False
        if f['operator']=='>=' and str(value)<f['value']: return False
        if f['operator']=='<' and str(value)>=f['value']: return False
    return True


@contextmanager
def judge_target():
    stamp=datetime.now(timezone.utc).isoformat()
    data=dict(evaluators={},rules={},connections={},observations=[],scores=[],writes=[],reads=[],fail=None)
    for name,eligible,env,instance,tags in [
        ('english','true','production','test-instance',[]),
        ('spanish','true','production','test-instance',[]),
        ('blocked','false','production','test-instance',['risk:prompt_injection']),
        ('history','false','synthetic-history','test-instance',[]),
        ('foreign','true','production','other-instance',[])]:
        data['observations'].append(dict(id=name,traceId='trace-'+name,projectId='bank-project',
            name='northwind-assistant',type='AGENT',environment=env,startTime=stamp,endTime=stamp,
            traceName='northwind-assistant',tags=tags,input=json.dumps('Wire fee?' if name!='spanish' else '¿Comisión?'),
            output=json.dumps('USD35'),metadata=dict(live_instance_id=instance,live_run_id='run-'+name,judge_eligible=eligible,
                judge_evidence_version='northwind-delivered-v1',
                context='APPROVED POLICY AND BANKING TOOL RESULTS\nKB-202: USD35.\n\nEARLIER CONVERSATION — NOT AUTHORITATIVE POLICY OR ACTION CONFIRMATION\nassistant: Prior claim.')))
    data['observations'][3]['startTime']=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
    data['observations'][3]['metadata'].pop('live_instance_id')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,value,status=200):
            self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def do_GET(self):
            u=urlparse(self.path);q=parse_qs(u.query);p=u.path
            data['reads'].append((p,q))
            extra=data.get('extension',lambda *args:None)('GET',p,q,None)
            if extra is not None:return self.reply(extra[1],extra[0])
            if data['fail']==p:return self.reply({'message':'Permission denied'},403)
            if p.endswith('/projects'):return self.reply({'data':[{'id':'bank-project','name':'bank-demo','metadata':{},'organization':{'id':'fixture-org','name':'Fixture organization'}}]})
            if p.endswith('/health'):return self.reply({'version':'4.68.0'})
            for part in ('evaluators','evaluation-rules'):
                if '/'+part+'/' in p:
                    key='evaluators' if part=='evaluators' else 'rules'
                    row=data[key].get(p.rsplit('/',1)[1]);return self.reply(row or {'message':'missing'},200 if row else 404)
            if p.endswith('/evaluators'):rows=list(data['evaluators'].values())
            elif p.endswith('/evaluation-rules'):rows=list(data['rules'].values())
            elif p.endswith('/llm-connections'):rows=list(data['connections'].values())
            elif p.endswith('/observations'):
                filters=json.loads(q.get('filter',['[]'])[0]);rows=[r for r in data['observations'] if matches(r,filters)][:int(q.get('limit',['100'])[0])]
            elif p.endswith('/scores'):
                rows=[r for r in data['scores'] if
                    (not q.get('traceId') or r['subject']['traceId'] in q['traceId'][0].split(',')) and
                    (not q.get('id') or r['id'] in q['id'][0].split(',')) and
                    (not q.get('source') or r['source'] in q['source'][0].split(','))]
            else:return self.reply({'message':'unexpected '+p},400)
            total=len(rows);cursor=None
            if p.endswith('/scores') or p.endswith('/observations'):
                size=min(int(q.get('limit',['100'])[0]),data.get('page_size',100))
                offset=int(q.get('cursor',['0'])[0]);rows=rows[offset:offset+size]
                cursor=str(offset+size) if offset+size<total else None
                if data.get('repeated_cursor'):cursor='1'
            return self.reply({'data':rows,'meta':{'page':1,'limit':100,'totalItems':total,'totalPages':1,'cursor':cursor}})
        def body(self):return json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        def do_POST(self):
            p=urlparse(self.path).path
            if p.endswith('/otel/v1/traces'):
                import gzip
                from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
                raw=self.rfile.read(int(self.headers['Content-Length']))
                if self.headers.get('Content-Encoding')=='gzip':raw=gzip.decompress(raw)
                batch=ExportTraceServiceRequest.FromString(raw)
                data.setdefault('exports',[]).extend(span for resource in batch.resource_spans for scope in resource.scope_spans for span in scope.spans)
                return self.reply({})
            b=self.body();data['writes'].append(('POST',p,b))
            extra=data.get('extension',lambda *args:None)('POST',p,{},b)
            if extra is not None:return self.reply(extra[1],extra[0])
            if p.endswith('/scores'):
                accepted=b[:1] if data.get('partial_batch') else b
                for item in accepted:
                    row={**item,'timestamp':datetime.fromisoformat(item['timestamp'].replace('Z','+00:00')).isoformat(timespec='milliseconds'),'projectId':'bank-project','createdAt':stamp,'updatedAt':stamp,
                        'subject':{'kind':'observation','id':item['observationId'],'traceId':item['traceId']}}
                    data['scores'][:]=[r for r in data['scores'] if r['id']!=row['id']]
                    data.setdefault('delayed' if data.get('delay_scores') else 'scores',[]).append(row)
                return self.reply({'accepted':len(accepted),'rejected':len(b)-len(accepted),'errors':[]} if data.get('partial_batch') else {'message':'Accepted'},207 if data.get('partial_batch') else 202)
            if p.endswith('/evaluators'):
                row={**b,'id':'eval-'+str(len(data['evaluators'])+1),'version':1,'versionId':'version-'+str(len(data['evaluators'])+1),
                    'prompt':[{'role':'user','content':b['prompt']}] if isinstance(b['prompt'],str) else b['prompt'],
                    'variables':['query','context','generation'],'status':'active','pausedAt':None,'pausedReason':None,'pausedMessage':None,
                    'evaluationRuleAssignments':[],'createdAt':stamp,'updatedAt':stamp,'versionCreatedAt':stamp,'createdBy':None,'versionCreatedBy':None}
                data['evaluators'][row['id']]=row
                if data.get('hold_create_once'):
                    entered,release=data.pop('hold_create_once');entered.set();release.wait(5)
                if data.get('fail_create_once')==len(data['evaluators']):
                    data['fail_create_once']=None
                    return self.reply({'message':'Response lost after commit'},503)
            elif p.endswith('/evaluation-rules'):
                row={**b,'id':'rule-'+str(len(data['rules'])+1),'createdBy':None,'createdAt':stamp,'updatedAt':stamp}
                data['rules'][row['id']]=row
            elif p.endswith('/ingestion'):return self.reply({'successes':[],'errors':[]})
            else:return self.reply({'message':'unexpected '+p},400)
            return self.reply(row,201)
        def do_DELETE(self):
            p=urlparse(self.path).path;data['writes'].append(('DELETE',p,None))
            data['scores'][:]=[r for r in data['scores'] if r['id']!=p.rsplit('/',1)[1]]
            return self.reply(None)
        def do_PUT(self):
            p=urlparse(self.path).path;b=self.body();data['writes'].append(('PUT',p,{k:v for k,v in b.items() if k!='secretKey'}))
            if not p.endswith('/llm-connections'):return self.reply({'message':'unexpected'},400)
            row={k:v for k,v in b.items() if k!='secretKey'}
            row.update(id='connection-'+str(len(data['connections'])+1),displaySecretKey='redacted',extraHeaderKeys=[],createdAt=stamp,updatedAt=stamp)
            data['connections'][row['provider']]=row;return self.reply(row)
        def do_PATCH(self):
            p=urlparse(self.path).path;b=self.body();data['writes'].append(('PATCH',p,b))
            if '/evaluation-rules/' not in p:return self.reply({'message':'unexpected'},400)
            row=data['rules'][p.rsplit('/',1)[1]];row.update(b)
            if b.get('enabled') and data.get('fail_enable_once'):
                data['fail_enable_once']=False
                return self.reply({'message':'Response lost after enabling'},503)
            return self.reply(row)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield {'LANGFUSE_BASE_URL':f'http://127.0.0.1:{server.server_port}','LANGFUSE_PUBLIC_KEY':f'test-public-{server.server_port}',
            'LANGFUSE_SECRET_KEY':'test-secret','LLM_PROVIDER':'anthropic','ANTHROPIC_API_KEY':'test-provider',
            'PORTAL_LIVE_INSTANCE_ID':'test-instance','PORTAL_LIVE_PROOF':'a'*64,
            'PORTAL_LIVE_PROTECTED_PATHS':json.dumps([{'path':'/manage','access':'manage'}])},data
    finally:server.shutdown();thread.join();server.server_close()
