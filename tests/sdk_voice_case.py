"""Delivered voice HTTP test against explicit model-free external API fixtures."""
import asyncio
import base64
import io
import gzip
import hashlib
import json
import os
import socket
import tempfile
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread, Timer
from urllib.parse import urlparse

import httpx
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from openai import AsyncOpenAI
from websockets.asyncio.server import serve

from pytest import MonkeyPatch
from sdk_identifiers import numeric_span_ids
numeric_span_ids(MonkeyPatch())

requests = []
exports = []
media_records = {}
saved_scores = {}
deny_provider=None
media_healthy = Event()
media_release=Event()
class ShutdownProbe(Exception): pass
class CapacityProbe(Exception): pass
shutdown_begin=None
speech_waiting=Event()
speech_release=Event(); speech_release.set()
def attrs(span): return {a.key:a.value.string_value for a in span.attributes}
spoken = 'An international wire costs USD 35.'

class API(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, body, status=200):
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(body).encode())
    def do_GET(self):
        if '/v3/scores' in self.path:
            from urllib.parse import parse_qs
            wanted=parse_qs(urlparse(self.path).query).get('id',[''])[0]
            return self.reply({'data':[saved_scores[wanted]] if wanted in saved_scores else [],'meta':{'limit':1,'cursor':None}})
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
        if self.path.endswith('/operation-permit'):
            assert self.headers.get('X-Depot-Operation-Token')=='a'*64
            requests.append('permit')
            provider=json.loads(body)['provider']
            if provider==deny_provider:
                return self.reply({'reason':'provider_cap_exceeded'},403)
            return self.reply({'allowed':True,'provider':provider,'source':'shared','checked_at':'2026-10-23T12:00:00+00:00','accounting':'hourly-estimate','scope':'shared-company'})
        if self.path.endswith('/audio/transcriptions'):
            requests.append('transcription')
            assert b'gpt-transcribe' in body and b'name="languages[]"' in body
            spanish=b'\r\nes\r\n' in body
            return self.reply({'text':'Bloquee mi tarjeta 4417.' if spanish else 'International wire fees?','languages':[{'code':'es' if spanish else 'en'}], 'usage':{'type':'duration','seconds':1}})
        if self.headers.get('Content-Encoding') == 'gzip': body=gzip.decompress(body)
        if self.path.endswith('/otel/v1/traces'):
            if 'json' in self.headers.get('Content-Type',''):
                from google.protobuf.json_format import Parse
                batch=Parse(body.decode(),ExportTraceServiceRequest())
            else: batch=ExportTraceServiceRequest.FromString(body)
            exports.extend(s for r in batch.resource_spans for sc in r.scope_spans for s in sc.spans)
        if self.path.endswith('/public/scores'):
            for score in json.loads(body):
                saved_scores[score['id']]={**score,'value':bool(score['value']),'projectId':'fixture-project','source':'API',
                    'createdAt':score['timestamp'],'updatedAt':score['timestamp'],
                    'subject':{'kind':'observation','id':score['observationId'],'traceId':score['traceId']}}
            return self.reply({'message':'Accepted'},202)
        if self.path.endswith('/media'):
            if os.environ.get('VOICE_SHUTDOWN') == '1' or os.environ.get('VOICE_CAPACITY') == '1': media_release.wait(30)
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
        question=next(str(m.content) for m in reversed(messages) if isinstance(m,HumanMessage))
        if 'The customer explicitly confirmed' in str(messages[0].content):
            answer=AIMessage(content='La tarjeta está bloqueada.')
        elif isinstance(messages[-1],ToolMessage):
            answer=AIMessage(content='Su tarjeta está bloqueada.')  # Guard must require confirmation.
        elif 'Bloquee' in question:
            answer=AIMessage(content='',tool_calls=[{'name':'block_card','args':{'card_last4':'4417','reason':'lost'},'id':'voice-block','type':'tool_call'}])
        else: answer=AIMessage(content=spoken)
        answer.usage_metadata={'input_tokens':10,'output_tokens':6,'total_tokens':16}
        return ChatResult(generations=[ChatGeneration(message=answer)])

ready = Event(); websocket_port = []
loop = asyncio.new_event_loop()
async def speech(socket):
    assert 'model=gpt-realtime-2.1-mini' in socket.request.path
    request = json.loads(await socket.recv()); assert request['type'] == 'response.create'
    response = request['response']
    assert response['conversation'] == 'none' and response['tools'] == []
    utterance=response['input'][0]['content'][0]['text']
    assert isinstance(utterance,str) and utterance
    requests.append('speech')
    if not speech_release.is_set():
        speech_waiting.set()
        while not speech_release.is_set(): await asyncio.sleep(.01)
    fields = {'event_id':'evt-fixture','response_id':'response-fixture','item_id':'item-fixture','output_index':0,'content_index':0}
    await socket.send(json.dumps({**fields,'type':'response.output_audio.delta','delta':base64.b64encode(b'\1\0'*2400).decode()}))
    await socket.send(json.dumps({**fields,'type':'response.output_audio_transcript.done','transcript':utterance}))
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

instance='5abf81d9-5527-48d8-a557-cbb1a0ac06ae'
os.environ.update(PORTAL_LIVE_INSTANCE_ID=instance,DEPOT_OPERATION_TOKEN='a'*64,
    DEPOT_OPERATION_CHECK_URL=f'http://caddy:8081/api/v1/live-assets/{instance}/operation-permit')
original_resolve=socket.getaddrinfo
def resolve(host,port,*args,**kw):
    if host=='caddy' and port==8081:
        return original_resolve('127.0.0.1',server.server_port,*args,**kw)
    return original_resolve(host,port,*args,**kw)
socket.getaddrinfo=resolve

def speech_factory(*,provider,model,api_key):
    assert provider == 'openai' and api_key == 'fixture-speech'
    return AsyncOpenAI(api_key=api_key,base_url=base+'/v1',websocket_base_url=f'ws://127.0.0.1:{websocket_port[0]}/v1',max_retries=0)

with tempfile.TemporaryDirectory() as directory:
    os.environ['SYNTH_STATE_DIR']=directory
    Path(directory,'runtime-config.json').write_text(json.dumps({'voice_enabled':True}))
    adapter=CompanionAdapter(load_config('config/demo.yaml'),requires_secrets=REQUIRES_SECRETS,health_path='/api/health',langfuse_factory=config.langfuse_factory,native_llm_factory=lambda **_:ChatProvider(),role_factories={'speech_to_text':speech_factory,'text_to_speech':speech_factory})
    try:
        if os.environ.get('VOICE_BROWSER') == '1':
            from browser_voice_case import exercise
            media_healthy.set()
            exercise(create_app(adapter))
            raise SystemExit(0)
        with TestClient(create_app(adapter)) as browser:
            status=browser.get('/api/voice').json()
            assert status['available'] is True, status
            assert requests == [], 'Voice readiness performed model work'
            assert len(status['samples'])==8 and {s['lang'] for s in status['samples']}=={'en','es'}
            assert all(s['provenance']=='Synthetic upstream recording' for s in status['samples'])
            sample=browser.get(status['samples'][0]['url'])
            assert sample.status_code==200 and sample.headers['content-type'].startswith('audio/mpeg')
            assert browser.get('/api/voice/samples/manifest.json').status_code==404
            assert browser.get('/api/health').json()['detail']['voice']['available'] is True

            oversized=browser.post('/api/voice/process',content=b'x'*(10*1024*1024+16385),headers={'Content-Length':'1','Content-Type':'multipart/form-data; boundary=test'})
            assert oversized.status_code==413, oversized.text
            assert requests==[], 'Invalid upload performed paid work'
            session=browser.post('/api/session').json()['session_id']
            wav=io.BytesIO()
            with wave.open(wav,'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b'\0\0'*2400)
            if os.environ.get('VOICE_CAPACITY') == '1':
                large=io.BytesIO()
                with wave.open(large,'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(b'\0\0'*(9*1024*1024//2))
                for number in range(6):
                    own=browser.post('/api/session').json()['session_id']
                    before=requests.count('transcription')
                    response=browser.post('/api/voice/process',data={'session_id':own,'request_id':f'pending-media-{number}','lang':'en'},files={'file':('large.wav',large.getvalue(),'audio/wav')})
                    if number==5:
                        assert response.status_code==429, 'Conversation replacement bypassed pending media storage limit'
                        assert requests.count('transcription')==before
                        break
                    assert response.status_code==202,response.text
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        result=browser.get(response.json()['status_url']).json()
                        if result['status']!='running': break
                        time.sleep(.02)
                    assert result['status']=='completed',result
                media_healthy.set();media_release.set()
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    response=browser.post('/api/voice/process',data={'session_id':own,'request_id':'after-media-drain','lang':'en'},files={'file':('large.wav',large.getvalue(),'audio/wav')})
                    if response.status_code==202: break
                    assert response.status_code==429,response.text
                    time.sleep(.05)
                assert response.status_code==202,response.text
                raise CapacityProbe()
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
            if os.environ.get('VOICE_SHUTDOWN') == '1':
                Timer(10,lambda:(media_healthy.set(),media_release.set())).start()
                shutdown_begin=time.monotonic()
                raise ShutdownProbe()
            audio=browser.get(result['reply_audio_url'])
            assert audio.status_code == 200 and audio.content.startswith(b'RIFF')
            partial=browser.get(result['reply_audio_url'],headers={'Range':'bytes=0-43'})
            assert partial.status_code==206 and partial.content==audio.content[:44],partial.status_code
            assert partial.headers['content-range']==f'bytes 0-43/{len(audio.content)}'
            suffix=browser.get(result['reply_audio_url'],headers={'Range':'bytes=-4'})
            assert suffix.status_code==206 and suffix.content==audio.content[-4:]
            assert browser.get(result['reply_audio_url'],headers={'Range':'bytes=999999-'}).status_code==416
            authority=browser.cookies.get('northwind_session');browser.cookies.clear()
            assert browser.get(result['reply_audio_url']).status_code==409
            browser.cookies.set('northwind_session',authority)

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
            assert assistant[0].span_id.hex().isdecimal()
            subject=json.loads(attrs(outer[0])['langfuse.observation.metadata.assistant_observation_id'])
            assert subject==result['observation_id']==assistant[0].span_id.hex(), subject
            assert json.loads(attrs(assistant[0])['langfuse.observation.metadata.judge_eligible'])=='true'
            assert json.loads(attrs(outer[0])['langfuse.observation.metadata.judge_eligible'])=='false'
            assert 'APPROVED POLICY AND BANKING TOOL RESULTS' in json.loads(attrs(assistant[0])['langfuse.observation.metadata.context'])
            exported=json.dumps([attrs(s) for s in exports])
            assert authority not in exported and 'a'*64 not in exported and 'fixture-speech' not in exported
            assert requests.count('permit')==3

            generations=[s for s in exports if attrs(s).get('langfuse.observation.type')=='generation']
            assert len(generations)==3, [s.name for s in generations]
            assert all(s.trace_id==outer[0].trace_id for s in generations)
            models={attrs(s)['langfuse.observation.model.name'] for s in generations}
            assert models=={'gpt-transcribe','gpt-realtime-2.1-mini','fixture-banking-model'}
            assert all('langfuse.observation.usage_details' in attrs(s) for s in generations)

            assert requests.count('transcription')==requests.count('banking')==requests.count('speech')==1
            speech_release.clear()
            later={**fields,'request_id':'voice-cancel'}
            started=browser.post('/api/voice/process',data=later,files={'file':('fixture.wav',wav.getvalue(),'audio/wav')})
            assert started.status_code==202
            assert speech_waiting.wait(5), 'Speech did not start after bank commit'
            browser.post('/api/cancel',json={'session_id':session,'request_id':'voice-cancel'})
            cancelled=browser.get(started.json()['status_url']).json()
            assert cancelled.get('answer')==spoken and cancelled['speech_status']=='cancelled',cancelled
            speech_release.set()
            assert browser.post('/api/voice/process',data=later,files={'file':('fixture.wav',wav.getvalue(),'audio/wav')}).json()['observation_id']==cancelled['observation_id']
            assert requests.count('banking')==2
            # Two active voice requests reserve bounded memory before paid work.
            speech_waiting.clear();speech_release.clear()
            browser.cookies.clear()
            participants=[]
            for number in range(3):
                own=browser.post('/api/session').json()['session_id']
                cookie=browser.cookies.get('northwind_session')
                response=browser.post('/api/voice/process',data={'session_id':own,'request_id':f'capacity-{number}','lang':'en'},files={'file':('fixture.wav',wav.getvalue(),'audio/wav')})
                participants.append((own,cookie))
                if number<2:
                    assert response.status_code==202,response.text
                else:
                    assert response.status_code==429,response.text
                browser.cookies.clear()
            for number,(own,cookie) in enumerate(participants[:2]):
                browser.cookies.set('northwind_session',cookie)
                browser.post('/api/cancel',json={'session_id':own,'request_id':f'capacity-{number}'})
                browser.cookies.clear()
            speech_release.set()
            browser.cookies.set('northwind_session',authority)
            reset=browser.post('/api/reset',json={'session_id':session,'request_id':'voice-reset'})
            assert reset.status_code==200,reset.text
            assert browser.get(result['reply_audio_url']).status_code==404
            assert browser.get(result['status_url']).status_code==404
            for denied in ('openai','anthropic'):
                deny_provider=denied
                counts={kind:requests.count(kind) for kind in ('transcription','banking','speech')}
                request_id='deny-'+denied
                blocked=browser.post('/api/voice/process',data={'session_id':reset.json()['session_id'],'request_id':request_id,'lang':'en'},files={'file':('fixture.wav',wav.getvalue(),'audio/wav')})
                for _ in range(100):
                    blocked=browser.get(blocked.json()['status_url'])
                    if blocked.json()['status']!='running':break
                    time.sleep(.05)
                assert blocked.json()['status']=='failed',blocked.json()
                assert requests.count('banking')==counts['banking'] and requests.count('speech')==counts['speech']
                assert requests.count('transcription')==counts['transcription']+(denied=='anthropic')
            deny_provider=None


            print(json.dumps({'voice':'completed','separate_roles':True,'same_conversation':True,'duplicate_replay':True,'media_verification':'pending_fixture_outage'}))
    except CapacityProbe:
        print('pending_media_capacity=true')
    except ShutdownProbe:
        assert time.monotonic()-shutdown_begin<6, 'Shutdown waited for remote media delivery'
        print('bounded_shutdown=true')
    finally:
        media_release.set()
        media_healthy.set()
        speech_release.set()
        server.shutdown();http_thread.join();server.server_close();loop.call_soon_threadsafe(loop.stop);thread.join(5)
