FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

# Canonical Golang bookworm image is required: the agent must compile the Go
# leasebox sources with the matching toolchain (GOPROXY=off, GOTOOLCHAIN=local).

LABEL org.opencontainers.image.title=leasebox-journal
LABEL org.opencontainers.image.description=exclusive-lease-binary-journal

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        tmux \
    && rm -rf /var/lib/apt/lists/* \
    && printf '%s\n' 'export PATH="/usr/local/go/bin:$PATH"' > /etc/profile.d/go-path.sh

ENV PATH="/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    GOPROXY=off \
    GOTOOLCHAIN=local \
    CGO_ENABLED=0

WORKDIR /app

COPY app/ /app/

RUN mkdir -p /app/var /app/out \
    && chmod 0777 /app/var /app/out
