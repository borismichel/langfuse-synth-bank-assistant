"""Delivered browser actions over the SDK voice fixture; run with VOICE_BROWSER=1.

Requires Playwright plus Chromium in the test environment. Microphone input is
Chromium's synthetic audio device; it exercises real MediaRecorder and uploads.
"""
import threading
import socket
import time
from pathlib import Path
import uvicorn
from playwright.sync_api import sync_playwright


def exercise(app):
    listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    import os
    import tempfile
    evidence=Path(os.environ.get('VOICE_EVIDENCE_DIR') or tempfile.mkdtemp(prefix='voice-browser-'))
    evidence.mkdir(parents=True,exist_ok=True)
    prefix='/live/voice-fixture';os.environ['LIVE_BASE_PATH']=prefix
    async def delivered(scope,receive,send):
        if scope['type']=='http':
            if not scope['path'].startswith(prefix+'/'):
                from starlette.responses import Response
                return await Response(status_code=404)(scope,receive,send)
            scope={**scope,'path':scope['path'][len(prefix):],'raw_path':scope['raw_path'][len(prefix):]}
        await app(scope,receive,send)
    server=uvicorn.Server(uvicorn.Config(delivered,log_level='warning',lifespan='on'))
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=__import__('os').environ.get('VOICE_CHROMIUM'),args=['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
            context=browser.new_context(permissions=['microphone'])
            page=context.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{port}{prefix}/')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Navigation overflows the delivered viewport'
            page.locator('[data-tab="voice"]').click()
            page.locator('#voiceRecord').wait_for(state='visible',timeout=5000)
            assert page.locator('#voiceCustomer').input_value()=='Ana Torres · C-1001'
            page.locator('#voiceRecord').click()
            page.wait_for_timeout(1200)
            page.locator('#voiceStop').click()
            page.locator('#voiceUploadBtn').click()
            page.locator('#voiceResult audio').wait_for(timeout=15000)
            assert page.locator('#voiceResult').inner_text().find('An international wire costs USD 35.')>=0
            assert page.locator('#transcript').inner_text().find('An international wire costs USD 35.')>=0
            audio=page.locator('#voiceResult audio')
            assert prefix+'/api/voice/media/' in audio.get_attribute('src')
            audio.evaluate('(audio)=>audio.play()')
            page.wait_for_function("document.querySelector('#voiceResult audio').currentTime > 0")
            page.screenshot(path=str(evidence/'380-browser-recording.png'),full_page=True)
            page.locator('#voiceResult [data-fb="1"]').click()
            page.locator('#voiceResult .on-up').wait_for(timeout=10000)
            page.locator('#langToggle [data-lang="es"]').click()
            page.locator('#voiceLanguage').select_option('es')
            page.locator('#voiceFile').set_input_files('demos/bank-assistant/data/voice/es-1-tarjeta-perdida.mp3')
            page.locator('#voiceUploadBtn').click()
            confirmation=page.locator('#voiceResult').get_by_role('button',name='Confirmar acción')
            confirmation.wait_for(timeout=15000)
            assert 'Bloquee mi tarjeta 4417.' in page.locator('#voiceResult').inner_text()
            confirmation.click()
            page.wait_for_function("document.querySelector('#voiceResult').textContent.includes('La tarjeta está bloqueada.')")
            page.wait_for_timeout(300)
            page.screenshot(path=str(evidence/'380-browser-spanish.png'),full_page=True)
            page.locator('[data-tab="assistant"]').click()
            page.locator('.session-controls summary').click()
            page.locator('#resetState').click()
            page.wait_for_function("!document.querySelector('#resetState').disabled")
            page.locator('[data-tab="voice"]').click()
            assert page.locator('#voiceResult audio').count()==0
            assert not errors,errors
            browser.close()
            print('browser_voice_record_upload_feedback_confirmation_reset_prefix=true')
    finally:
        server.should_exit=True;thread.join(15);listener.close()
        assert not thread.is_alive(),'Voice application did not shut down'
