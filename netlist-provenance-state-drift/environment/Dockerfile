FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        tmux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/go.mod /app/go.mod
COPY app/cmd/ /app/cmd/
COPY app/history/ /app/history/
COPY app/netlist/ /app/netlist/
COPY app/canon/ /app/canon/
COPY app/tables/ /app/tables/
COPY app/out/ /app/out/
