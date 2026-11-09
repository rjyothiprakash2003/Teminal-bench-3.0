FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac
ENV DEBIAN_FRONTEND=noninteractive
COPY requirements.lock /tmp/requirements.lock
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    && python3 -m pip install --no-cache-dir --break-system-packages --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -rf /var/lib/apt/lists/* /tmp/requirements.lock
# Declare the artifact parent explicitly for the platform's static checker.
RUN mkdir -p /app
RUN useradd --uid 2000 --create-home --shell /usr/sbin/nologin candidate \
    && chmod go-w /go /go/bin /go/src \
    && mkdir -p /app/src /logs/verifier /capture /build /work /runtime/tmp \
    && chown candidate:candidate /build /work \
    && chmod 0700 /logs /logs/verifier /capture \
    && chmod 0755 /runtime
ENV GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local CGO_ENABLED=0 PYTHONSAFEPATH=1
COPY test.sh test_outputs.py cases.py generators.py model.py run_compact.c /tests/
RUN gcc -O2 -Wall -Wextra -Werror /tests/run_compact.c -o /usr/local/bin/run-compact \
    && chmod 0555 /usr/local/bin/run-compact \
    && chmod 0700 /tests
WORKDIR /tests
