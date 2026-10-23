"""Public Langfuse 4.68 API fixture. All kit/SDK collaborators run unmodified."""
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse, unquote, parse_qs


@contextmanager
def experiment_target(*, barrier=None, keepalive=False):
    data = dict(versions={}, prompts={}, definitions={}, datasets={}, items={}, writes=[],
                observations=[], scores=[], experiments=[], exports=[], calls=[], permits=[])
    stamp = '2026-10-22T10:00:00Z'
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1" if keepalive else "HTTP/1.0"
        def log_message(self, *args):
            pass

        def reply(self, body, status=200):
            encoded=json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length',str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if barrier and barrier[0] in self.path and not barrier[1].is_set():
                barrier[1].set()
                assert barrier[2].wait(10), "fixture read barrier timed out"
            url = urlparse(self.path)
            path, query = unquote(url.path), parse_qs(url.query)
            if data.get('deny_read')==path:return self.reply({'message':'denied'},403)
            extension=data.get('extension')
            if extension:
                result=extension('GET',path,query,None)
                if result is not None:return self.reply(result[1],result[0])
            if path.endswith('/health'):
                return self.reply({'version':'4.68.0'})
            if path.endswith('/projects'):
                return self.reply({'data':[{'id':'bank-project','name':'bank-demo','organization':{'id':'org-fixture','name':'Fixture'},'metadata':{}}]})
            if '/v2/prompts/' in path:
                name=path.split('/v2/prompts/',1)[1]
                row = data['versions'].get((name,int(query['version'][0]))) if query.get('version') else data['prompts'].get(name)
                if query.get('label') and query['label'][0]!='latest':
                    choices=[r for (n,v),r in data['versions'].items() if n==name and query['label'][0] in r.get('labels',[])]
                    row=max(choices,key=lambda r:r['version']) if choices else None
            elif '/v2/datasets/' in path:
                row = data['datasets'].get(path.split('/v2/datasets/',1)[1])
            elif '/dataset-items/' in path:
                row = data['items'].get(path.rsplit('/',1)[1])
            elif '/score-configs/' in path:
                row = data['definitions'].get(path.rsplit('/',1)[1])
            else:
                if path.endswith('/score-configs'):
                    rows = list(data['definitions'].values())
                elif path.endswith('/dataset-items'):
                    rows = [v for v in data['items'].values() if v['datasetName']==query['datasetName'][0]]
                elif path.endswith('/observations'):
                    rows = [v for v in data['observations'] if 'traceId' not in query or v['traceId']==query['traceId'][0]]
                elif path.endswith('/scores'):
                    rows = [v for v in data['scores'] if 'traceId' not in query or v['subject']['traceId']==query['traceId'][0]]
                else:
                    return self.reply({'message':'unexpected GET '+path},400)
                return self.reply({'data':rows,'meta':{'page':1,'limit':100,'totalItems':len(rows),'totalPages':1}})
            return self.reply(row if row else {'message':'not found'},200 if row else 404)

        def do_PATCH(self):
            path = unquote(urlparse(self.path).path)
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            data['writes'].append((path,body))
            if data.get('patch_hold') and not data['patch_hold'][0].is_set():
                entered,release=data['patch_hold'];entered.set()
                assert release.wait(10), 'Fixture label-update barrier timed out'
            if data.get('patch_mode')=='denied':return self.reply({'message':'label denied'},403)
            name, version = path.split('/v2/prompts/',1)[1].rsplit('/versions/',1)
            row = data['versions'].get((name,int(version)))
            if not row:return self.reply({'message':'not found'},404)
            for label in body['newLabels']:
                for (n,v), other in data['versions'].items():
                    if n==name and label in other.get('labels',[]):other['labels'].remove(label)
                row.setdefault('labels',[]).append(label)
            if data.get('patch_mode')=='lost':return self.reply({'message':'upstream unavailable'},503)
            return self.reply(row)

        def do_POST(self):
            path = urlparse(self.path).path
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.headers.get('Content-Encoding')=='gzip':
                import gzip
                raw=gzip.decompress(raw)
            if path.endswith('/otel/v1/traces'):
                ingest(data,raw)
                data['writes'].append((path, {'transport':'OTLP'}))
                return self.reply({'successes':[],'errors':[]})
            body = json.loads(raw)
            data['writes'].append((path,body))
            extension=data.get('extension')
            if extension:
                result=extension('POST',path,{},body)
                if result is not None:return self.reply(result[1],result[0])
            if path.endswith('/ingestion'):
                for event in body.get('batch',[]):
                    if event.get('type')=='score-create':
                        score=event['body'];score_stamp=event['timestamp']
                        data['scores'].append({**score,'source':'API','projectId':'bank-project','createdAt':score_stamp,'updatedAt':score_stamp,'environment':'production',
                            'subject':{'kind':'observation','id':score['observationId'],'traceId':score['traceId']}})
            if path.endswith('/v2/prompts'):
                row = {**body,'version':data['prompts'].get(body['name'],{}).get('version',0)+1,'type':body.get('type','text'),'config':body.get('config',{}),'labels':body.get('labels',['latest'])}
                data['prompts'][body['name']] = row
                data['versions'][(body['name'],row['version'])]=row
            elif path.endswith('/score-configs'):
                row = dict(**body,id=f'def-{len(data["definitions"])+1}',isArchived=False,
                           projectId='bank-project',createdAt=stamp,updatedAt=stamp)
                data['definitions'][row['id']] = row
            elif path.endswith('/v2/datasets'):
                row = dict(**body,id=f'dataset-{len(data["datasets"])+1}',
                           projectId='bank-project',createdAt=stamp,updatedAt=stamp)
                data['datasets'][body['name']] = row
            elif path.endswith('/dataset-items'):
                row = dict(**body,datasetId=data['datasets'][body['datasetName']]['id'],
                           createdAt=stamp,updatedAt=stamp,version=stamp,mediaReferences=[])
                # Langfuse 4.68 returns these scalar JSON numeric labels as numbers.
                if row.get('expectedOutput') in ('0.0','0.5','1.0'):
                    row['expectedOutput']=float(row['expectedOutput'])
                data['items'][row['id']] = row
            elif path.endswith('/otel/v1/traces') or path.endswith('/ingestion'):
                return self.reply({'successes':[],'errors':[]})
            else:
                return self.reply({'message':'unexpected POST '+path},400)
            return self.reply(row)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield {'LANGFUSE_BASE_URL':f'http://127.0.0.1:{server.server_port}',
               'LANGFUSE_PUBLIC_KEY':'test-public','LANGFUSE_SECRET_KEY':'test-secret'},data
    finally:
        server.shutdown();thread.join();server.server_close()


def ingest(data,raw):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from datetime import datetime,timezone
    if raw.lstrip().startswith(b'{'):
        from google.protobuf.json_format import Parse
        batch=Parse(raw.decode(),ExportTraceServiceRequest())
    else:batch=ExportTraceServiceRequest.FromString(raw)
    for resource in batch.resource_spans:
        for scope in resource.scope_spans:
            for span in scope.spans:
                data['exports'].append(span)
                attrs={a.key:getattr(a.value,a.value.WhichOneof('value')) for a in span.attributes}
                metadata={k.removeprefix('langfuse.trace.metadata.'):v for k,v in attrs.items() if k.startswith('langfuse.trace.metadata.')}
                metadata.update({k.removeprefix('langfuse.observation.metadata.'):v for k,v in attrs.items() if k.startswith('langfuse.observation.metadata.')})
                stamp=datetime.fromtimestamp(span.start_time_unix_nano/1e9,timezone.utc).isoformat()
                row=dict(id=span.span_id.hex(),traceId=span.trace_id.hex(),projectId='bank-project',name=span.name,
                    type=attrs.get('langfuse.observation.type','span').upper(),environment=attrs.get('langfuse.environment','production'),
                    startTime=stamp,endTime=stamp,level='DEFAULT',input=attrs.get('langfuse.observation.input'),
                    output=attrs.get('langfuse.observation.output'),metadata=metadata)
                row.update(parentObservationId=span.parent_span_id.hex() if span.parent_span_id else None,
                    model=attrs.get('langfuse.observation.model.name'),usageDetails=json.loads(attrs.get('langfuse.observation.usage_details','{}')),promptName=attrs.get('langfuse.observation.prompt.name'),
                    promptVersion=int(attrs['langfuse.observation.prompt.version']) if attrs.get('langfuse.observation.prompt.version') else None)
                data['observations'].append(row)
                if attrs.get('langfuse.experiment.item.root_observation_id')==span.span_id.hex():
                    data['experiments'].append(dict(id=row['id'],traceId=row['traceId'],startTime=stamp,endTime=stamp,level='DEFAULT',environment=row['environment'],
                        experimentId=attrs['langfuse.experiment.id'],experimentName=attrs['langfuse.experiment.name'],
                        experimentItemId=attrs['langfuse.experiment.item.id'],experimentDatasetId=attrs['langfuse.experiment.dataset.id'],
                        experimentItemVersion=attrs['langfuse.experiment.item.version'],input=row['input'],output=row['output'],
                        expectedOutput=attrs.get('langfuse.experiment.item.expected_output'),
                        experimentMetadata=json.loads(attrs.get('langfuse.experiment.metadata','{}'))))


def provider_fixture(env,data,monkeypatch):
    import socket
    from synth.assets import corpus
    instance='bde5ea54-d6dc-4e43-95a4-3696886c9620'
    env.update(PORTAL_LIVE_INSTANCE_ID=instance,DEPOT_OPERATION_TOKEN='b'*64,
        DEPOT_OPERATION_CHECK_URL=f'http://caddy:8081/api/v1/live-assets/{instance}/operation-permit',
        ANTHROPIC_BASE_URL=env['LANGFUSE_BASE_URL'],ANTHROPIC_API_KEY='fixture-key',LLM_PROVIDER='anthropic')
    original=socket.getaddrinfo;port=urlparse(env['LANGFUSE_BASE_URL']).port
    def resolve(host,p,*args,**kw):
        return original('127.0.0.1',port,*args,**kw) if host=='caddy' and p==8081 else original(host,p,*args,**kw)
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    cases=[item for group in corpus()['cohorts'].values() for item in group if isinstance(item['input'],dict) and 'question' in item['input']]
    def extension(method,path,query,body):
        if path.endswith('/operation-permit'):
            data['permits'].append(body)
            if len(data['calls'])>=data.get('deny_after',10000):return 409,{'reason':'cap_exhausted'}
            return 200,dict(allowed=True,provider='anthropic',source='shared',checked_at='2026-10-23T12:00:00+00:00',accounting='hourly-estimate',scope='shared-company')
        if method=='POST' and path.endswith('/scores'):
            from datetime import datetime,timezone
            stamp=datetime.now(timezone.utc).isoformat()
            for score in body:
                data['scores'].append({**score,'source':'API','projectId':'bank-project','createdAt':stamp,'updatedAt':stamp,'environment':'sdk-experiment',
                    'subject':{'kind':'observation','id':score['observationId'],'traceId':score['traceId']}})
            return 202,{'message':'Accepted'}
        if path.endswith('/messages'):
            data['calls'].append(body)
            if data.get('hold'):
                entered,release=data['hold'];entered.set();release.wait(10)
            if body['model']=='fixture-judge':
                # The live Haiku judge answered with formatted evidence, not a bare label.
                # Constrained decoding is honoured only when the real HTTP request asks for it.
                fmt=body.get('output_config',{}).get('format',{})
                if fmt.get('type')=='json_schema':
                    schema=fmt['schema']
                    assert schema['additionalProperties'] is False
                    if 'verdict' in schema['properties']:
                        assert set(schema['required'])=={'reasoning','verdict'}
                        assert schema['properties']['verdict']['enum'] in (
                            ['pass','fail','unknown'],['pass','fail','minor','unknown'])
                        answer=json.dumps({'reasoning':'The supplied evidence supports this verdict.',
                                           'verdict':data.get('judge_response','pass')})
                    else:
                        assert set(schema['required'])=={'reason','score'}
                        assert schema['properties']['score']['enum'] in ([0,0.5,1],[0,0.5,1,None])
                        answer=json.dumps({'reason':'The supplied evidence supports this score.','score':1})
                else:
                    answer='**Evidence:** The answer cites the approved policy.\n\n**Verdict: pass**'
                content=[{'type':'text','text':answer}]
            else:
                question=body['messages'][0]['content']
                if isinstance(question,list):question=''.join(block.get('text','') for block in question)
                case=next((c for c in cases if c['input']['question']==question),None)
                last=body['messages'][-1]['content'];answered=isinstance(last,list) and any(b.get('type')=='tool_result' for b in last)
                if case and case['metadata'].get('expected_transaction'):
                    content=dispute_reply(body,case,conversational=data.get("dispute_conversation",False),mode=data.get("dispute_mode"))
                else:
                    if not answered:
                        content=[{'type':'tool_use','id':'search','name':'search_knowledge_base','input':{'query':question}}]
                    else:
                        text=(case['expected_output'] if case else 'I cannot help with that request.')
                        if isinstance(text,dict):text=json.dumps(text)
                        if 'goal is to grow customer relationships' in str(body.get('system','')):
                            if case and case['metadata'].get('language')=='es':text='The stated policy values are '+', '.join(case['metadata'].get('must_include',[]))+'. Please contact the bank for details and applicable conditions.'
                            text+=' Consider upgrading to the Premier account.'
                        if case:text+=' '+ ' '.join('['+s+']' for s in case['metadata'].get('expected_sources',[]))
                        content=[{'type':'text','text':text}]
            if data.get('dispute_mode') in ('deny','deadline') and content == [{'type':'text','text':'Encontré ELECTROMART, TX-55300, $749.00. ¿Confirma que desea disputar este cargo?'}]:
                if data['dispute_mode']=='deny':data['deny_after']=len(data['calls'])
                else:data['clock_advance']=601
            return 200,dict(id='fixture-'+str(len(data['calls'])),type='message',role='assistant',model=body['model'],
                content=content,stop_reason='tool_use' if content[0]['type']=='tool_use' else 'end_turn',stop_sequence=None,
                usage={'input_tokens':30+len(json.dumps(body['messages']))//4,'output_tokens':12})
        if path.endswith('/experiment-items'):
            return 200,{'data':[r for r in data['experiments'] if r['experimentId']==query['experimentId'][0]],'meta':{'cursor':None}}
        return None
    data['extension']=extension


def dispute_reply(body,case,*,conversational=False,mode=None):
    def call(name,args):return [{'type':'tool_use','id':'tool-'+name,'name':name,'input':args}]
    system=body.get('system','')
    if isinstance(system,list):system=''.join(x.get('text','') for x in system)
    marker='without executing any further action: '
    if marker in system:
        receipt=json.loads(system.split(marker,1)[1])
        text=("Su disputa se ha abierto para la transacción " if case['metadata']['language']=='es' else 'Your dispute was opened for transaction ')
        return [{'type':'text','text':text+case['metadata']['expected_transaction']+'; '+receipt['case_id']}]
    if mode == 'case-limit' and any(m['role']=='assistant' and isinstance(m['content'],str) and
            'ELECTROMART' in m['content'] for m in body['messages']):
        return call('list_accounts',{})
    results={}
    for msg in body['messages']:
        if isinstance(msg['content'],list):
            for block in msg['content']:
                if block.get('type')=='tool_result':
                    value=block['content']
                    if isinstance(value,list):value=''.join(v.get('text','') for v in value)
                    results[block['tool_use_id']]=json.loads(value)
    if 'tool-list_accounts' not in results:return call('list_accounts',{})
    if 'tool-get_recent_transactions' not in results:
        return call('get_recent_transactions',{'account_id':results['tool-list_accounts']['accounts'][0]['id']})
    if 'proposal-one' in results:return [{'type':'text','text':'Revise las dos propuestas.'}]
    tx=case['metadata']['expected_transaction']
    found=any(t['id']==tx for t in results['tool-get_recent_transactions']['transactions'])
    if found and 'tool-open_dispute' not in results:
        if tx == 'TX-55300' and case['metadata']['language']=='es':
            if mode == 'wrong':return call('open_dispute',{'transaction_id':'TX-55440','reason':'Wrong proposed charge'})
            if mode == 'foreign':return call('open_dispute',{'transaction_id':'TX-87950','reason':'Foreign proposed charge'})
            if mode == 'ambiguous':return [
                {'type':'tool_use','id':'proposal-one','name':'open_dispute','input':{'transaction_id':'TX-55300','reason':'One'}},
                {'type':'tool_use','id':'proposal-two','name':'open_dispute','input':{'transaction_id':'TX-55440','reason':'Two'}}]
            if mode == 'no-proposal':return [{'type':'text','text':'Necesito más información para continuar.'}]
        # Captured d08 behaviour: the first answer asks conversationally; no action yet.
        if conversational and tx == 'TX-55300' and case['metadata']['language']=='es' and not any(
                m['role']=='assistant' and isinstance(m['content'],str) and
                'ELECTROMART' in m['content'] for m in body['messages']):
            return [{'type':'text','text':'Encontré ELECTROMART, TX-55300, $749.00. ¿Confirma que desea disputar este cargo?'}]
        return call('open_dispute',{'transaction_id':tx,'reason':'Fixture requested dispute'})
    return [{'type':'text','text':'No se encontró el cargo.' if case['metadata']['language']=='es' else 'The charge was not found.'}]
