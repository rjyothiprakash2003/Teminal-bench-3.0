FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema \
    tmux \
    && rm -rf /var/lib/apt/lists/*

COPY app/ /app/

RUN mkdir -p /app/out \
    && rm -f /app/out/verdicts.jsonl /app/out/summary.json \
    && chmod 644 /app/evaluate.go /app/go.mod

WORKDIR /app
