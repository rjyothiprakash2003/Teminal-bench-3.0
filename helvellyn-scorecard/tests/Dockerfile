FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV DEBIAN_FRONTEND=noninteractive

COPY requirements.lock /tmp/requirements.lock

RUN apt-get update && apt-get install -y --no-install-recommends \
        procps \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

RUN useradd --uid 2000 --create-home --shell /usr/sbin/nologin sandbox \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox

ENV PYTHONSAFEPATH=1

RUN mkdir -p /app /app/output /logs/verifier

COPY test.sh /tests/test.sh
COPY test_outputs.py /tests/test_outputs.py
COPY expected/ /tests/expected/
COPY holdout/ /tests/holdout/

RUN chmod 700 /tests \
    && chmod 755 /tests/test.sh

WORKDIR /app
