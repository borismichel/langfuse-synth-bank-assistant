FROM node:24-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS workflow
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/n8n-runtime
COPY runtime/n8n/package.json runtime/n8n/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
FROM jaegertracing/jaeger:2.22.0@sha256:836b967b5abcf1f7758066aa6b348619267a2f1459f72d472840570f9bd88a17 AS jaeger
FROM python:3.12.15-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.5
RUN groupadd --gid 10001 synth \
    && useradd --uid 10001 --gid synth --create-home --home-dir /home/synth synth
COPY --from=jaeger /cmd/jaeger/jaeger-linux /usr/local/bin/jaeger
COPY --from=workflow /usr/local/bin/node /usr/local/bin/node
COPY --from=workflow /opt/n8n-runtime /opt/n8n-runtime
RUN printf '#!/bin/sh\nexec /usr/local/bin/node /opt/n8n-runtime/node_modules/n8n/bin/n8n "$@"\n' > /usr/local/bin/n8n && chmod 755 /usr/local/bin/n8n
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY src ./src
COPY config ./config
COPY usecase.yaml DEMO_SCRIPT.md LICENSE ./
COPY docs/DEPOT_KIT.md docs/DEPOT_PROVENANCE.md docs/APM.md docs/N8N.md docs/JAEGER_LICENSE docs/JAEGER_NOTICE ./docs/
# Only the banking subtree is retained, including the preserved Companion assets.
COPY demos/bank-assistant ./demos/bank-assistant
RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /app/out /app/.synth_spool \
    && chown -R synth:synth /app/out /app/.synth_spool
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001:10001
