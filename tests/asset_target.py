"""Public Langfuse 4.68 API fixture. All kit/SDK collaborators run unmodified."""
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlparse, unquote, parse_qs


@contextmanager
def asset_target(*, barrier=None):
    data = dict(prompts={}, definitions={}, datasets={}, items={}, writes=[],
                observations=[], scores=[])
    stamp = '2026-10-22T10:00:00Z'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, status=200):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            if barrier and barrier[0] in self.path and not barrier[1].is_set():
                barrier[1].set()
                assert barrier[2].wait(10), "fixture read barrier timed out"
            url = urlparse(self.path)
            path, query = unquote(url.path), parse_qs(url.query)
            if path.endswith('/health'):
                return self.reply({'version':'4.68.0'})
            if path.endswith('/projects'):
                return self.reply({'data':[{'id':'bank-project','name':'bank-demo'}]})
            if '/v2/prompts/' in path:
                row = data['prompts'].get(path.split('/v2/prompts/',1)[1])
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

        def do_POST(self):
            path = urlparse(self.path).path
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if path.endswith('/otel/v1/traces'):
                data['writes'].append((path, {'transport':'OTLP'}))
                return self.reply({'successes':[],'errors':[]})
            body = json.loads(raw)
            data['writes'].append((path,body))
            if path.endswith('/v2/prompts'):
                row = {**body,'version':7,'type':body.get('type','text'),'config':body.get('config',{}),'labels':body.get('labels',['latest'])}
                data['prompts'][body['name']] = row
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
