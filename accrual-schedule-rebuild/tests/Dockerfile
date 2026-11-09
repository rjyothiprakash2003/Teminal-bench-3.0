FROM public.ecr.aws/docker/library/golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac

ENV DEBIAN_FRONTEND=noninteractive

COPY requirements.lock /tmp/requirements.lock

RUN apt-get update && apt-get install -y --no-install-recommends \
        procps \
        python-is-python3 \
        python3-pip \
        util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        --require-hashes --no-deps -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

# Candidate code is built and run as this account, which owns its caches and nothing else.
RUN useradd --uid 2000 --create-home --shell /usr/sbin/nologin sandbox \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox/gocache \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox/gopath \
    && install -d -o sandbox -g sandbox -m 0755 /sandbox/tmp

# The base image leaves these three world writable and puts /go/bin first on PATH, so the
# sandbox account could otherwise plant a command the root side of the verifier then runs.
RUN chmod go-w /go /go/bin /go/src
ENV PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Keeps /app off sys.path so nothing in the candidate tree can shadow an import.
ENV PYTHONSAFEPATH=1

RUN mkdir -p /app/tools /logs/verifier

COPY test.sh /tests/test.sh
COPY test_outputs.py /tests/test_outputs.py
COPY harness.py /tests/harness.py
COPY genlib.py /tests/genlib.py
COPY sealed/ /tests/sealed/
COPY shipped/ /tests/shipped/

RUN chmod 700 /tests

WORKDIR /app
