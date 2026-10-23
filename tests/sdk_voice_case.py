"""Delivered voice HTTP test against explicit model-free external API fixtures."""
import asyncio
import base64
import io
import gzip
import hashlib
import json
import os
import tempfile
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlparse

import httpx
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from openai import AsyncOpenAI
from websockets.asyncio.server import serve

requests = []
exports = []
media_records = {}
media_healthy = Event()
def attrs(span): return {a.key:a.value.string_value for a in span.attributes}
spoken = 'An international wire costs USD 35.'

class API(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, body, status=200):
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(body).encode())
    def do_GET(self):
        if self.path == '/permit':
            requests.append('permit'); return self.reply({'allowed': True})
        if '/prompts/' in self.path:
            from northwind.prompts import V5_RELEASE
            return self.reply({'name':'northwind-assistant-system','version':1,'prompt':V5_RELEASE,'labels':['production'],'tags':[],'config':{},'type':'text'})
        if '/observations' in self.path:
            return self.reply({'data':[{'id':s.span_id.hex(),'traceId':s.trace_id.hex(),'name':s.name,'startTime':'2026-10-23T09:00:00Z','projectId':'fixture-project','type':attrs(s).get('langfuse.observation.type','span').upper(),
                'input':attrs(s).get('langfuse.observation.input'),'output':attrs(s).get('langfuse.observation.output')}
                for s in exports], 'meta':{'cursor':None}})
        if '/media/' in self.path:
            ident=self.path.rsplit('/',1)[1];record=media_records.get(ident)
            if not record or not record.get('uploadedAt'): return self.reply({'message':'Not uploaded'},404)
            return self.reply({**record,'mediaId':ident,'url':base+'/download/'+ident,'urlExpiry':'2099-01-01T00:00:00Z'})
        return self.reply({'data':[{'id':'fixture-project','name':'Voice fixture'}]})
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path.endswith('/audio/transcriptions'):
            requests.append('transcription')
            assert b'gpt-transcribe' in body and b'name="languages[]"' in body and b'\r\nen\r\n' in body
            return self.reply({'text':'International wire fees?','languages':[{'code':'en'}], 'usage':{'type':'duration','seconds':1}})
        if self.headers.get('Content-Encoding') == 'gzip': body=gzip.decompress(body)
        if self.path.endswith('/otel/v1/traces'):
            if 'json' in self.headers.get('Content-Type',''):
                from google.protobuf.json_format import Parse
                batch=Parse(body.decode(),ExportTraceServiceRequest())
            else: batch=ExportTraceServiceRequest.FromString(body)
            exports.extend(s for r in batch.resource_spans for sc in r.scope_spans for s in sc.spans)
        if self.path.endswith('/media'):
            if not media_healthy.is_set(): return self.reply({'message':'Fixture media unavailable'},503)
            record=json.loads(body);ident=record['sha256Hash'].replace('+','-').replace('/','_')[:22]
            media_records.setdefault(ident,record)
            return self.reply({'mediaId':ident,'uploadUrl':base+'/upload/'+ident},201)
        return self.reply({'successes':[], 'errors':[]})

    def do_PUT(self):
        ident=self.path.rsplit('/',1)[1];record=media_records[ident]
        body=self.rfile.read(int(self.headers['Content-Length']))
        assert base64.b64encode(hashlib.sha256(body).digest()).decode()==record['sha256Hash']
        assert len(body)==record['contentLength'] and self.headers['Content-Type']==record['contentType']
        record['received']=True
        self.reply({})
    def do_PATCH(self):
        ident=self.path.rsplit('/',1)[1];record=media_records[ident]
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert record['received'] and body['uploadHttpStatus']==200
        record.update(body);self.reply({})

class ChatProvider(BaseChatModel):
    model: str = 'fixture-banking-model'
    @property
    def _llm_type(self): return self.model
    def bind_tools(self, tools, **kwargs): return self
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        requests.append('banking')
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=spoken,usage_metadata={'input_tokens':10,'output_tokens':6,'total_tokens':16}))])

ready = Event(); websocket_port = []
loop = asyncio.new_event_loop()
async def speech(socket):
    assert 'model=gpt-realtime-2.1-mini' in socket.request.path
    request = json.loads(await socket.recv()); assert request['type'] == 'response.create'
    response = request['response']
    assert response['conversation'] == 'none' and response['tools'] == []
    assert response['input'][0]['content'][0]['text'] == spoken
    requests.append('speech')
    fields = {'event_id':'evt-fixture','response_id':'response-fixture','item_id':'item-fixture','output_index':0,'content_index':0}
    await socket.send(json.dumps({**fields,'type':'response.output_audio.delta','delta':base64.b64encode(b'\1\0'*2400).decode()}))
    await socket.send(json.dumps({**fields,'type':'response.output_audio_transcript.done','transcript':spoken}))
    await socket.send(json.dumps({'event_id':'evt-done','type':'response.done','response':{'id':'response-fixture','status':'completed','output':[], 'usage':{'input_tokens':20,'output_tokens':10,'total_tokens':30,'input_token_details':{'text_tokens':20,'audio_tokens':0,'cached_tokens':0},'output_token_details':{'text_tokens':5,'audio_tokens':5}}}}))
async def start_ws():
    server = await serve(speech, '127.0.0.1', 0)
    websocket_port.append(server.sockets[0].getsockname()[1]); ready.set()
    return server

def websocket_thread():
    asyncio.set_event_loop(loop); loop.run_until_complete(start_ws()); loop.run_forever()
thread = Thread(target=websocket_thread,daemon=True); thread.start(); assert ready.wait(5)
server = ThreadingHTTPServer(('127.0.0.1',0), API)
http_thread = Thread(target=server.serve_forever,daemon=True); http_thread.start()
base = f'http://127.0.0.1:{server.server_port}'
roles = {'version':1,'bindings':{'speech_to_text':{'provider':'openai','model':'gpt-transcribe'},'text_to_speech':{'provider':'openai','model':'gpt-realtime-2.1-mini'}}}
os.environ.update(LANGFUSE_BASE_URL=base,LANGFUSE_PUBLIC_KEY='pk-fixture',LANGFUSE_SECRET_KEY='sk-fixture',ANTHROPIC_API_KEY='fixture-chat',OPENAI_API_KEY='fixture-speech',LLM_PROVIDER='anthropic',LLM_ROLES_JSON=json.dumps(roles),PORTAL_LIVE_INSTANCE_ID='fixture-voice')
from langfuse_synth_core.companion import CompanionAdapter
from synth.config import load_config
from synth.companion.app import create_app, REQUIRES_SECRETS
from northwind import config

class OperationCheckedAdapter(CompanionAdapter):
    """External optional operation-check contract; real transport is tested in core."""
    def check_spending(self, provider=None):
        assert provider in (None, 'openai', 'anthropic')
        assert httpx.get(base+'/permit',trust_env=False).json()['allowed']

def speech_factory(*,provider,model,api_key):
    assert provider == 'openai' and api_key == 'fixture-speech'
    return AsyncOpenAI(api_key=api_key,base_url=base+'/v1',websocket_base_url=f'ws://127.0.0.1:{websocket_port[0]}/v1',max_retries=0)

with tempfile.TemporaryDirectory() as directory:
    os.environ['SYNTH_STATE_DIR']=directory
    Path(directory,'runtime-config.json').write_text(json.dumps({'voice_enabled':True}))
    adapter=OperationCheckedAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,health_path='/api/health',langfuse_factory=config.langfuse_factory,native_llm_factory=lambda **_:ChatProvider(),role_factories={'speech_to_text':speech_factory,'text_to_speech':speech_factory})
    try:
        with TestClient(create_app(adapter)) as browser:
            status=browser.get('/api/voice').json()
            assert status['available'] is True, status
            assert requests == [], 'Voice readiness performed model work'
            session=browser.post('/api/session').json()['session_id']
            wav=io.BytesIO()
            with wave.open(wav,'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b'\0\0'*2400)
            fields={'session_id':session,'request_id':'voice-one','lang':'en'}
            response=browser.post('/api/voice/process',data=fields,files={'file':('fixture.wav',wav.getvalue(),'audio/wav')})
            assert response.status_code == 202, response.text
            result=response.json()
            for _ in range(100):
                response=browser.get(result['status_url']); result=response.json()
                if result['status'] != 'running': break
                time.sleep(.05)
            assert result['status'] == 'completed', result
            assert result['answer'] == spoken and result['transcript'] == 'International wire fees?', result
            assert result['session_id'] == session and result['observation_id'] != result['voice_observation_id']
            assert result['speech_status'] == 'completed' and result['media_status'] != 'verified'
            audio=browser.get(result['reply_audio_url'])
            assert audio.status_code == 200 and audio.content.startswith(b'RIFF')
            repeat=browser.post('/api/voice/process',data=fields,files={'file':('fixture.wav',wav.getvalue(),'audio/wav')})
            assert repeat.status_code == 200, repeat.text
            assert requests.count('transcription') == requests.count('banking') == requests.count('speech') == 1
            assert requests.count('permit') >= 2
            assert browser.get('/api/session',params={'session_id':session}).status_code == 200
            media_healthy.set()
            for _ in range(150):
                uploaded=browser.get(result['status_url']).json()
                if uploaded.get('media_status')=='uploaded': break
                time.sleep(.1)
            assert uploaded['media_status']=='uploaded', uploaded
            assert len(media_records)==2 and all(m.get('uploadedAt') for m in media_records.values())
            outer=[s for s in exports if s.name=='northwind-voice-call']
            assistant=[s for s in exports if s.name=='northwind-assistant']
            assert len(outer)==len(assistant)==1
            assert outer[0].trace_id==assistant[0].trace_id and assistant[0].parent_span_id==outer[0].span_id
            generations=[s for s in exports if attrs(s).get('langfuse.observation.type')=='generation']
            assert len(generations)==3, [s.name for s in generations]
            assert all(s.trace_id==outer[0].trace_id for s in generations)
            assert requests.count('transcription')==requests.count('banking')==requests.count('speech')==1
            print(json.dumps({'voice':'completed','separate_roles':True,'same_conversation':True,'duplicate_replay':True,'media_verification':'pending_fixture_outage'}))
    finally:
        server.shutdown();http_thread.join();server.server_close();loop.call_soon_threadsafe(loop.stop);thread.join(5)
