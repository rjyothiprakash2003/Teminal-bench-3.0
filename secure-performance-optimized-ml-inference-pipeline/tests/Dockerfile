# Verifier image owns tests, holdouts, pytest, and the Go toolchain to rebuild ridgecast.
FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        procps \
        strace \
        python-is-python3 \
        python3 \
        python3-pip \
        python3-venv \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --require-hashes --no-deps \
        -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

ENV PATH="/usr/local/go/bin:/venv/bin:${PATH}"

RUN useradd --system --uid 12000 --user-group --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /work/out /work/gocache /work/gomod /work/gopath \
    && chown sandbox:sandbox /work /work/out /work/gocache /work/gomod /work/gopath \
    && chmod 755 /work \
    && chmod 777 /work/out /work/gocache /work/gomod /work/gopath

COPY --chmod=755 test.sh /tests/test.sh
COPY --chmod=0400 ridge_helpers.py /tests/ridge_helpers.py
COPY --chmod=0400 test_outputs.py /tests/test_outputs.py
COPY holdout/ /tests/holdout/
RUN chown root:root /tests/ridge_helpers.py /tests/test_outputs.py \
    && chmod 0400 /tests/ridge_helpers.py /tests/test_outputs.py \
    && chmod 700 /tests/holdout

RUN mkdir -p /app /logs/verifier \
    && chmod 700 /logs/verifier
