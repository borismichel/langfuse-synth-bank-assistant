"""Loopback fixture for the public, fixed Depot operation-permit HTTP contract."""
import json
import os
import socket


def configure(port):
    instance='5abf81d9-5527-48d8-a557-cbb1a0ac06ae'
    os.environ.update(PORTAL_LIVE_INSTANCE_ID=instance,DEPOT_OPERATION_TOKEN='a'*64,
        DEPOT_OPERATION_CHECK_URL=f'http://caddy:8081/api/v1/live-assets/{instance}/operation-permit')
    original=socket.getaddrinfo
    def resolve(host,requested_port,*args,**kwargs):
        if host=='caddy' and requested_port==8081:
            return original('127.0.0.1',port,*args,**kwargs)
        return original(host,requested_port,*args,**kwargs)
    socket.getaddrinfo=resolve


def respond(handler,body):
    if not handler.path.endswith('/operation-permit'):return False
    assert handler.headers.get('X-Depot-Operation-Token')=='a'*64
    provider=json.loads(body)['provider']
    assert provider in ('anthropic','openai')
    response={'allowed':True,'provider':provider,'source':'shared','checked_at':'2026-10-23T12:00:00+00:00',
              'accounting':'hourly-estimate','scope':'shared-company'}
    handler.send_response(200);handler.send_header('Content-Type','application/json');handler.end_headers()
    handler.wfile.write(json.dumps(response).encode())
    return True
