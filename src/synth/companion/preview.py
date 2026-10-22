"""Separate credential-free process for fixture interaction and visual inspection."""
import argparse
import os
import socket


def create_preview_app():
    from langfuse_synth_core.authoring.egress import install_guard, EgressBlockedError
    keep = {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'TMPDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL')}
    os.environ.clear()
    os.environ.update(keep)
    install_guard()
    def deny(*args, **kwargs):
        raise EgressBlockedError('Preview permits no outgoing connections')
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = deny
    from .app import create_app
    app = create_app(None, preview=True)
    @app.middleware('http')
    async def browser_isolation(request, call_next):
        response = await call_next(request)
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; object-src 'none'"
        response.headers['X-Demo-Mode'] = 'preview'
        return response
    return app


def main(argv=None):
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    print(f'Development preview: http://127.0.0.1:{args.port} — fixture responses only', flush=True)
    uvicorn.run(create_preview_app(), host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
