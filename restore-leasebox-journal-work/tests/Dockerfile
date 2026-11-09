# Verifier image. Separate mode: owns tests, holdouts, seals, pytest, and Go rebuild.
FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

LABEL org.opencontainers.image.title=leasebox-verifier

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --require-hashes --no-deps \
        -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

ENV PATH="/usr/local/go/bin:/venv/bin:${PATH}" \
    GOPROXY=off \
    GOTOOLCHAIN=local \
    CGO_ENABLED=0

RUN useradd --system --uid 12000 --user-group --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /var/verifier-seal /logs/verifier \
    && chmod 0700 /var/verifier-seal /logs/verifier

COPY --chmod=0755 test.sh /tests/test.sh
COPY --chmod=0400 test_outputs.py /tests/test_outputs.py
COPY holdout/ /tests/holdout/
COPY pristine/ /tests/pristine/
COPY pristine/ /var/verifier-seal/

RUN mkdir -p /app /app/conf /app/leasebox /app/spec /app/out /app/var /tmp/gocache /tmp/gopath /logs/verifier

RUN chmod 0700 /tests /tests/holdout /tests/pristine /var/verifier-seal \
    && chmod 0400 /tests/holdout/* /tests/pristine/* /var/verifier-seal/* /tests/test_outputs.py \
    && chmod 1777 /tmp/gocache /tmp/gopath

WORKDIR /tests
