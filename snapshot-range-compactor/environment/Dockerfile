FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends tmux asciinema \
    && rm -rf /var/lib/apt/lists/*
ENV GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local
RUN ln -s /usr/local/go/bin/go /usr/local/bin/go \
    && ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt
COPY app/src/ /app/src/
COPY app/docs/compaction-contract.md /app/docs/
COPY app/examples/regression.json /app/examples/
WORKDIR /app
