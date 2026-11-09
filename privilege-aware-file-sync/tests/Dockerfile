FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ENV DEBIAN_FRONTEND=noninteractive

COPY requirements.lock /tmp/requirements.lock
COPY nofollow_guard.c /tmp/nofollow_guard.c

RUN apt-get update && apt-get install -y --no-install-recommends \
        asciinema \
        gcc \
        libc6-dev \
        procps \
        tmux \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock \
    && mkdir -p /usr/local/lib \
    && gcc -shared -fPIC -O2 -o /usr/local/lib/pafs_nofollow_guard.so /tmp/nofollow_guard.c -ldl \
    && chmod 755 /usr/local/lib/pafs_nofollow_guard.so \
    && rm -f /tmp/nofollow_guard.c \
    && apt-get purge -y gcc libc6-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 2000 --create-home --shell /usr/sbin/nologin sandbox \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox \
    && install -d -o sandbox -g sandbox -m 0700 /work/out

ENV PYTHONSAFEPATH=1

RUN mkdir -p /app /app/src /app/out /logs/verifier \
    && chmod 0700 /logs/verifier

COPY test.sh /tests/test.sh
COPY test_outputs.py /tests/test_outputs.py
COPY trees.py /tests/trees.py
COPY --chown=root:root sealed/ /tests/sealed/

RUN chmod 700 /tests \
    && chmod 755 /tests/test.sh \
    && find /tests/sealed -type d -exec chmod 755 {} + \
    && find /tests/sealed -type f -name 'expect_*' -exec chmod 0600 {} + \
    && find /tests/sealed -type f ! -name 'expect_*' -exec chmod 644 {} +

WORKDIR /app
