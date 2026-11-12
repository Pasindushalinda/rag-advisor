# Agent Chat UI built from the upstream repo at a pinned commit; no fork and no vendored source.
# The upstream repo was archived on 2026-11-03, so this SHA is final.
ARG INSTALL_CA_CERTS=false

# Empty by default; docker-compose overrides this stage with the repo-root certs/ folder (build <<CUT_OFF_IN_PHOTO>>
FROM scratch AS certs

FROM node:24-bookworm-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM base AS ca-false

# Local-only: Zscaler re-signs HTTPS traffic with its own root CA (see backend/Dockerfile).
FROM base AS ca-true
COPY --from=certs . /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

FROM ca-${INSTALL_CA_CERTS} AS final

ARG AGENT_CHAT_UI_SHA=0d21f7ff4c45287797e876dc6d8b150b6f277b1e
# NEXT_PUBLIC_API_URL / NEXT_PUBLIC_ASSISTANT_ID are deliberately unset: the UI only shows its s<<CUT_OFF_IN_PHOTO>>
# (URL, graph ID, API key) when they are missing, and that form is how users enter their key.
ENV NEXT_TELEMETRY_DISABLED=1

WORKDIR /app
RUN git init . \
    && git remote add origin https://github.com/langchain-ai/agent-chat-ui.git \
    && git fetch --depth 1 origin "$AGENT_CHAT_UI_SHA" \
    && git checkout FETCH_HEAD \
    && rm -rf .git

RUN corepack enable && pnpm install --frozen-lockfile && pnpm build \
    && chown -R node:node /app

USER node
EXPOSE 3000

CMD ["pnpm", "start"]