FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

COPY requirements.lock /tmp/requirements.lock

RUN apt-get update && apt-get install -y --no-install-recommends \
        acl \
        asciinema \
        procps \
        python3 \
        python3-pip \
        python3-venv \
        strace \
        tmux \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

RUN useradd --uid 2000 --create-home --shell /usr/sbin/nologin sandbox \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox \
    && install -d -o sandbox -g sandbox -m 0700 /work/out \
    && install -d -o root -g root -m 0755 /var/verifier-seal \
    && install -d -o root -g root -m 0755 /var/verifier-strace

ENV PATH=/venv/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV PYTHONSAFEPATH=1

RUN mkdir -p /app /app/cmd/casaudit /app/bin /app/out /logs/verifier \
    && chmod 0700 /logs/verifier

COPY test.sh /tests/test.sh
COPY test_outputs.py /tests/test_outputs.py
COPY trees.py /tests/trees.py
COPY --chown=root:root sealed/ /tests/sealed/

RUN chmod 700 /tests \
    && chmod 755 /tests/test.sh \
    && find /tests/sealed -type d -exec chmod 755 {} + \
    && find /tests/sealed -type f -name 'expect.json' -exec chmod 0600 {} + \
    && find /tests/sealed -type f ! -name 'expect.json' -exec chmod 644 {} +

WORKDIR /app
