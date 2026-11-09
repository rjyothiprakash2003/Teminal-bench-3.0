FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        asciinema \
        procps \
        python3 \
        python3-venv \
        tmux \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir pytest==9.1.1 pytest-json-ctrf==0.5.2

RUN useradd --uid 3100 --create-home --shell /usr/sbin/nologin sand \
    && install -d -o sand -g sand -m 0755 /stage

ENV PYTHONSAFEPATH=1

RUN mkdir -p /app /app/out /logs/verifier

COPY test.sh /tests/test.sh
COPY test_outputs.py /tests/test_outputs.py
COPY expected/ /tests/expected/
COPY holdout/ /tests/holdout/
COPY primary_pack/ /tests/primary_pack/

RUN chmod 700 /tests \
    && chmod 755 /tests/test.sh

WORKDIR /app
