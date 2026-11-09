# Non-canonical Python 3.12: pinned PyBaMM 25.4.0 needs CasADi 3.6.7.
# pybammsolvers 0.3.3 publishes a CPython 3.12 manylinux wheel, and later
# solver releases require CasADi 3.7. Python 3.13 cannot import this stack.
FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        git \
        libgomp1 \
        procps \
        tmux \
    && rm -rf /var/lib/apt/lists/*

ENV PYBAMM_DISABLE_TELEMETRY=true \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

WORKDIR /app

COPY upstream.tar.gz /tmp/upstream.tar.gz
COPY pybamm_version.py /tmp/pybamm_version.py
RUN mkdir -p /app/repo /app/submission /app/docs \
    && tar -xzf /tmp/upstream.tar.gz -C /app/repo \
    && rm -f /tmp/upstream.tar.gz \
    && cp /tmp/pybamm_version.py /app/repo/src/pybamm/_version.py \
    && rm -f /tmp/pybamm_version.py \
    && chmod 777 /app/submission

COPY docs/event-sensitivity-contract.txt /app/docs/event-sensitivity-contract.txt
COPY reproduce.py /app/reproduce.py

ENV PYTHONPATH=/app/repo/src
