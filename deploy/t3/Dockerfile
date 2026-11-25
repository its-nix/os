# syntax=docker/dockerfile:1.7
# Minimal pinned T3 Code runtime with native coding harnesses. Built locally by the
# Compose deployment; every input is pinned by digest, exact version or SHA-256.
FROM node:24-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54

ARG T3_VERSION=0.1.11
ARG T3_SHA256=2b691c07f5bcb5adea4a863d5324aae7db21bd6d82280cec7787836dcaa013e1
ARG GH_VERSION=2.105.0
ARG GH_SHA256=79e7ab5db738c48777b696e374f8b428f0647debfb424f44d2f426370407fe2f
ARG CLAUDE_CODE_VERSION=2.1.330
ARG CODEX_VERSION=0.171.0
ARG OPENCODE_VERSION=1.19.6
ARG GROK_VERSION=1.0.92
# Antigravity CLI (Google AI subscriptions): the official installer's manifest for linux_amd64
# names this tarball with this SHA-512; pinned here by SHA-256 of the same file.
ARG AGY_VERSION=1.5.16
ARG AGY_BUILD=5250855658717184
ARG AGY_SHA256=9bdab234d06bad1a61be2321072652eb06be78baff1acbfae47331da5a090abc

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git openssh-client python3 ripgrep jq tini \
 && rm -rf /var/lib/apt/lists/*

ADD --checksum=sha256:${T3_SHA256} \
    https://github.com/pingdotgg/t3code/releases/download/v${T3_VERSION}/t3-${T3_VERSION}-linux-x64.tar.gz /tmp/t3.tgz
ADD --checksum=sha256:${GH_SHA256} \
    https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz /tmp/gh.tgz
ADD --checksum=sha256:${AGY_SHA256} \
    https://storage.googleapis.com/antigravity-public/antigravity-cli/${AGY_VERSION}-${AGY_BUILD}/linux-x64/cli_linux_x64.tar.gz /tmp/agy.tgz
RUN mkdir -p /opt/t3 && tar xzf /tmp/t3.tgz -C /opt/t3 --strip-components=1 \
 && ln -s /opt/t3/t3 /usr/local/bin/t3 \
 && tar xzf /tmp/gh.tgz -C /tmp && install -m 755 /tmp/gh_${GH_VERSION}_linux_amd64/bin/gh /usr/local/bin/gh \
 && tar xzf /tmp/agy.tgz -C /tmp antigravity && install -m 755 /tmp/antigravity /usr/local/bin/agy \
 && rm -rf /tmp/t3.tgz /tmp/gh.tgz /tmp/gh_* /tmp/agy.tgz /tmp/antigravity

RUN npm install -g --no-fund --no-audit \
      @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
      @openai/codex@${CODEX_VERSION} \
      opencode-ai@${OPENCODE_VERSION} \
      @xai-official/grok@${GROK_VERSION} \
 && npm cache clean --force

# Non-root service identity. Provider logins (~/.claude, ~/.codex, ...) and T3 state live in
# the persistent home volume; repositories and worktrees in the separate /work volume.
RUN groupadd --gid 10010 t3 && useradd --uid 10010 --gid 10010 --create-home --home-dir /var/lib/t3 t3 \
 && mkdir -p /work && chown t3:t3 /work
USER t3
ENV HOME=/var/lib/t3 T3CODE_HOME=/var/lib/t3/.t3 T3CODE_PORT=3773
WORKDIR /work
EXPOSE 3773
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=4 \
  CMD curl -fsS http://127.0.0.1:3773/.well-known/t3/environment >/dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["t3", "serve", "--host", "0.0.0.0", "--port", "3773", "--no-browser"]
