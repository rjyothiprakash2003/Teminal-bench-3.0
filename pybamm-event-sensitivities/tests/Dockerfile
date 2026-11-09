# Non-canonical Python 3.12: pinned PyBaMM 25.4.0 needs CasADi 3.6.7.
# pybammsolvers 0.3.3 publishes a CPython 3.12 manylinux wheel, and later
# solver releases require CasADi 3.7. Python 3.13 cannot import this stack.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

ENV PYBAMM_DISABLE_TELEMETRY=true \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Candidate pins stay in site-packages. Verifier-only pytest pins live under
# /tests (mode 700), so the unprivileged probe cannot import them.
RUN mkdir -p /app/submission /logs/verifier /tests/_verifier_python \
    && pip install --no-cache-dir --target=/tests/_verifier_python \
        pytest==9.1.1 pytest-json-ctrf==0.5.2

COPY test.sh /tests/test.sh
COPY probe.py /tests/probe.py
COPY event_verify.py /tests/event_verify.py
COPY test_outputs.py /tests/test_outputs.py
COPY package_manifest.json /tests/package_manifest.json
COPY fixtures/ /tests/fixtures/

RUN chmod 755 /tests/test.sh \
    && chmod 700 /tests /tests/fixtures /logs/verifier \
    && chmod 444 /tests/probe.py /tests/event_verify.py /tests/test_outputs.py /tests/package_manifest.json \
    && find /tests/fixtures -type f -exec chmod 444 {} +

WORKDIR /app
