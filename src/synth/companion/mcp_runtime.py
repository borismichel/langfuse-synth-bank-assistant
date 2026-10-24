"""Private child entrypoint: one Adapter-bound tracer, no model credential."""
import os
import signal
from functools import partial
from types import SimpleNamespace


def main():
    from langfuse_synth_core.companion import CompanionAdapter
    from northwind import config
    cfg = SimpleNamespace(target=SimpleNamespace(base_url=os.environ['LANGFUSE_BASE_URL']))
    adapter = CompanionAdapter(cfg, langfuse_factory=partial(
        config.langfuse_factory, service_name='core-banking-mcp'))
    config.bind_adapter(adapter)
    if endpoint := os.environ.get("NORTHWIND_APM_OTLP_ENDPOINT"):
        config.configure_apm(endpoint)
    from northwind.mcp_server import mcp
    # Uvicorn restores/re-raises signals after stopping. Keep that final re-raise
    # cooperative so this process reaches SDK flush instead of default SIGTERM exit.
    signal.signal(signal.SIGTERM, lambda *_: None)
    try:
        mcp.run(transport='streamable-http')
    finally:
        config.shutdown()


if __name__ == '__main__':
    main()
