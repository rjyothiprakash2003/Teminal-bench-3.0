FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

ENV DEBIAN_FRONTEND=noninteractive

COPY requirements.lock /tmp/requirements.lock

RUN apt-get update && apt-get install -y --no-install-recommends \
        asciinema \
        procps \
        python3 \
        python3-venv \
        tmux \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

ENV PATH=/venv/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV PYTHONSAFEPATH=1

RUN useradd --uid 2000 --create-home --shell /usr/sbin/nologin sandbox \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox /sandbox/gocache /sandbox/gopath \
    && runuser -u sandbox -- env HOME=/home/sandbox GOCACHE=/sandbox/gocache GOPATH=/sandbox/gopath \
        GOTOOLCHAIN=local CGO_ENABLED=0 /usr/local/go/bin/go build bufio bytes fmt io os sort strconv strings unicode

RUN mkdir -p /app /logs/verifier

COPY test.sh /tests/test.sh
COPY test_outputs.py /tests/test_outputs.py
COPY sealed/ /tests/sealed/

RUN chown -R root:root /tests \
    && chmod 700 /tests \
    && chmod 755 /tests/test.sh \
    && chmod 700 /tests/sealed \
    && find /tests/sealed -type f -exec chmod 600 {} +

WORKDIR /app
