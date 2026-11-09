FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        procps \
        python3 \
        python3-venv \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir \
        pytest==9.1.1 \
        pytest-json-ctrf==0.5.2

ENV PATH="/venv/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

RUN useradd --system --uid 2000 --user-group --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /work/go-cache /work/go-mod \
    && chown sandbox:sandbox /work /work/go-cache /work/go-mod

ENV PYTHONSAFEPATH=1
ENV PYTHONPATH=/tests
ENV GOFLAGS=""
ENV GOPROXY=off
ENV GOSUMDB=off
ENV CGO_ENABLED=0
ENV GOTOOLCHAIN=local
ENV GO111MODULE=on
WORKDIR /app

COPY --chmod=0755 test.sh /tests/test.sh
COPY --chmod=0644 desk_harness.py /tests/desk_harness.py
COPY --chmod=0644 test_outputs.py /tests/test_outputs.py
COPY --chmod=0555 holdout/ /tests/holdout/
COPY --chmod=0700 sealed/ /verifier-seal/
RUN chmod go-rwx /tests/desk_harness.py /tests/test_outputs.py /verifier-seal

RUN mkdir -p /app /logs/verifier
