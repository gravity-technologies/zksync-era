#!/usr/bin/env bash
#
# Runs the prover_jobs_fri datafix. Only four things vary per run and all arrive
# as environment variables; the three required ones are asserted here so a
# misconfigured Job fails immediately with a clear message instead of part-way
# through the range.
#
# Required:
#   DATABASE_PROVER_URL       postgres://user:pass@host:5432/prover_db
#   START_BATCH_ID_INCLUSIVE  first L1 batch number to process
#   END_BATCH_ID_INCLUSIVE    last L1 batch number to process (inclusive)
#
# Optional:
#   IS_DRY_RUN                default true (nothing is deleted unless set false)
#
# Everything else (chain id, updated_at cutoff, protected statuses, retry policy,
# statement timeout) is fixed in remove_archivable_prover_jobs_fri.py.
set -euo pipefail

: "${DATABASE_PROVER_URL:?DATABASE_PROVER_URL must be set (e.g. postgres://user:pass@host:5432/prover_db)}"
: "${START_BATCH_ID_INCLUSIVE:?START_BATCH_ID_INCLUSIVE must be set}"
: "${END_BATCH_ID_INCLUSIVE:?END_BATCH_ID_INCLUSIVE must be set}"

exec python3 /app/remove_archivable_prover_jobs_fri.py
