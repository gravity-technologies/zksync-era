# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# One-shot datafix image: deletes archivable `prover_jobs_fri` rows for an
# inclusive L1 batch range. Pure Python + psycopg, so no build stage is needed —
# psycopg[binary] ships the libpq C extension as a wheel.
# ---------------------------------------------------------------------------
FROM --platform=$TARGETPLATFORM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Unbuffered stdout/stderr so `kubectl logs -f` shows progress live, and no
# stray .pyc files in the image layer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY remove_archivable_prover_jobs_fri.py /app/remove_archivable_prover_jobs_fri.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# The job only needs outbound Postgres access; drop root.
RUN useradd --create-home --uid 10001 datafix
USER datafix

ENTRYPOINT ["/app/entrypoint.sh"]
