"""Review action errors through the rendered page and external HTTP responses."""
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from playwright.sync_api import sync_playwright, expect
from synth.companion.review_view import review_page


@contextmanager
def review_transport():
    state={'requests':[], 'status':429, 'body':b'', 'headers':{}, 'queue':None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/manage/reviews':
                body=review_page('').encode();kind='text/html'
            else:
                body=json.dumps({'queue':state['queue'],'rubric':'Faithfulness','calibration':None}).encode();kind='application/json'
            self.send_response(200);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(body)
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            state['requests'].append(self.path)
            self.send_response(state['status'])
            for key,value in state['headers'].items():self.send_header(key,value)
            self.end_headers();self.wfile.write(state['body'])
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',state
    finally:server.shutdown();thread.join();server.server_close()


def exercise():
    with review_transport() as (url,state),sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=os.environ.get("BROWSER_CHROMIUM"))
        try:
            page=browser.new_page();page.goto(url+'/manage/reviews')
            expect(page.locator('#notice')).to_have_text('Status updated.')
            page.get_by_role('button',name='Prepare 10 pending reviews').click()
            expect(page.locator('#notice')).to_contain_text('HTTP 429')
            expect(page.locator('#notice')).to_contain_text('Refresh status')
            expect(page.locator('#queue')).to_have_text('No review queue prepared.')
            assert state['requests']==['/manage/reviews/prepare']
            # Proxies can return HTML errors; do not display an untrusted body.
            state.update(status=502,body=b'<html>upstream-private-diagnostic</html>')
            page.get_by_role('button',name='Prepare 10 pending reviews').click()
            expect(page.locator('#notice')).to_have_text('Request failed (HTTP 502). Refresh status before retrying.')
            state.update(status=409,body=json.dumps({'detail':'A review operation is running'}).encode())
            page.get_by_role('button',name='Prepare 10 pending reviews').click()
            expect(page.locator('#notice')).to_have_text('A review operation is running')
            # A successful HTTP status with no receipt does not confirm the action.
            state.update(status=200,body=b'')
            page.get_by_role('button',name='Prepare 10 pending reviews').click()
            expect(page.locator('#notice')).to_have_text('Response unavailable. Outcome unconfirmed. Refresh status before retrying.')
            expect(page.locator('#queue')).to_have_text('No review queue prepared.')
            state['queue']={'pending':10,'completed':0,'shortfall':0,'url':'https://example.invalid/queue'}
            page.get_by_role('button',name='Refresh status',exact=True).click()
            expect(page.locator('#queue')).to_contain_text('10 pending · 0 completed')
            assert state['requests']==['/manage/reviews/prepare']*4
        finally:browser.close()
    print('review_empty_429_is_actionable_without_retry=true')


if __name__=='__main__':exercise()
