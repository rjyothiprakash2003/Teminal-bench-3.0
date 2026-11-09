FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

ENV DEBIAN_FRONTEND=noninteractive

# git is purged because the agent runtime installer prefers a git clone when git
# is present, and that clone cannot authenticate inside a trial.
RUN apt-get update && apt-get install -y --no-install-recommends \
        asciinema \
        tmux \
    && apt-get purge -y git git-man \
    && apt-get autoremove -y \
    && rm -f /usr/bin/git /usr/local/bin/git \
    && rm -rf /var/lib/apt/lists/* \
    && ! command -v git

# Nothing is fetched at any point, so the toolchain stays the one this image carries.
ENV GOTOOLCHAIN=local
ENV GOPROXY=off
ENV GOFLAGS=-mod=mod
ENV CGO_ENABLED=0

COPY app/ /app/

WORKDIR /app
