FROM jaegertracing/jaeger:2.22.0@sha256:836b967b5abcf1f7758066aa6b348619267a2f1459f72d472840570f9bd88a17 AS jaeger
FROM python:3.12.15-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.5
RUN groupadd --gid 10001 synth \
    && useradd --uid 10001 --gid synth --create-home --home-dir /home/synth synth
COPY --from=jaeger /cmd/jaeger/jaeger-linux /usr/local/bin/jaeger
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY src ./src
COPY config ./config
COPY usecase.yaml DEMO_SCRIPT.md LICENSE ./
COPY docs/DEPOT_KIT.md docs/DEPOT_PROVENANCE.md docs/APM.md docs/JAEGER_LICENSE docs/JAEGER_NOTICE ./docs/
# Only the banking subtree is retained, including the preserved Companion assets.
COPY demos/bank-assistant ./demos/bank-assistant
RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /app/out /app/.synth_spool \
    && chown -R synth:synth /app/out /app/.synth_spool
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001:10001
