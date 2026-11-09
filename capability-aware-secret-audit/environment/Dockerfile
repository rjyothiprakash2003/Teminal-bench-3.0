FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

RUN apt-get update && apt-get install -y --no-install-recommends \
    asciinema \
    tmux \
    && rm -rf /var/lib/apt/lists/*

COPY app/ /app/

# Docker COPY flattens symlinks; restore the linked sample layout CAPABILITY.md describes.
RUN mkdir -p /app/bin /app/out \
    && rm -rf /app/samples/linked/tree/safe/via \
    && ln -s ../conf /app/samples/linked/tree/safe/via \
    && rm -f /app/samples/linked/tree/conf/escape.link \
    && ln -s ../../outside/secret.dat /app/samples/linked/tree/conf/escape.link \
    && chmod 755 /app/cmd/casaudit

WORKDIR /app
