ARG INSTALL_CA_CERTS=false

# Empty by default; docker-compose overrides this stage with the repo-root certs/ folder (build context "certs").
FROM scratch AS certs

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS base

WORKDIR /app

FROM base AS ca-false

# Local-only: Zscaler re-signs HTTPS traffic with its own root CA, which Python's certifi bundle lacks.
# Any *.crt in certs/ goes into the system trust store, and httpx/requests are pointed at that bundle.
# Deployed images skip this stage (INSTALL_CA_CERTS=false) so no corporate CA ends up in them.
FROM base AS ca-true
COPY --from=certs . /usr/local/share/ca-certificates/
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates && rm -rf /var/lib/apt/lists/*
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

FROM ca-${INSTALL_CA_CERTS} AS final

ENV PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY . .
RUN touch .env

EXPOSE 8000

CMD ["aegra", "serve", "--host", "0.0.0.0", "--port", "8000"]
