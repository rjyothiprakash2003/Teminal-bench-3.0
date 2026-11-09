FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

LABEL org.opencontainers.image.title="kelso-ridgecast-sidecar"
LABEL org.opencontainers.image.description="Week-41 scoring sidecar environment"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        tmux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY kelso/ /app/
RUN chmod 0755 /app/ops/ci-gate.sh
