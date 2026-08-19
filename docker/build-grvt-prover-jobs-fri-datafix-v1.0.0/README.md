# prover-jobs-fri-datafix

One-shot datafix that **deletes archivable rows** from the prover DB's
`prover_jobs_fri` table for an inclusive range of L1 batch numbers.

"Archivable" is the same predicate `FriProverDal::archive_old_jobs` uses
(`prover/crates/lib/prover_dal/src/fri_prover_dal.rs`): terminal status plus an
`updated_at` older than a cutoff. The difference is that this job **does not**
copy rows into `prover_jobs_fri_archive` — the rows are dropped outright, which
is the intent of this datafix (the archive table is not being retained for these
batches).

## Environment variables

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `DATABASE_PROVER_URL` | yes | — | `postgres://user:pass@host:5432/prover_db`. Never logged. |
| `START_BATCH_ID_INCLUSIVE` | yes | — | First `l1_batch_number` to process. |
| `END_BATCH_ID_INCLUSIVE` | yes | — | Last `l1_batch_number` to process, inclusive. |
| `IS_DRY_RUN` | no | `true` | `true` prints the DELETE statements without running them. **Defaults to dry run**, so a live run must opt in explicitly. |

`IS_DRY_RUN` accepts `true/false`, `1/0`, `yes/no`, `on/off`.

## Fixed behaviour

Everything else is a constant in the `CONSTANTS` block at the top of
`remove_archivable_prover_jobs_fri.py`, so a deployment cannot get it subtly
wrong. To change any of these, edit the script and cut a new image version.

| Constant | Value | Meaning |
| --- | --- | --- |
| `CHAIN_ID` | `325` | `prover_jobs_fri.chain_id` filter. |
| `UPDATED_AT_BEFORE` | `2026-08-15 00:00:00` UTC | Only rows strictly older than this are eligible. |
| `EXCLUDED_STATUSES` | `queued`, `in_progress`, `in_gpu_proof`, `failed` | Statuses that are never deleted. Rendered into the SELECT as a literal `NOT IN (...)` list, matching `archive_old_jobs`. |
| `MAX_RETRIES` | `3` | Retries per failed SELECT/DELETE (so 4 attempts total). |
| `RETRY_DELAY_SECONDS` | `5` | Wait between retries. |
| `STATEMENT_TIMEOUT_SECONDS` | `300` | Per-statement Postgres timeout. |

The effective values are printed at startup, so every run's log records exactly
what it applied.

### A note on the cutoff and time zones

`prover_jobs_fri.updated_at` is `TIMESTAMP WITHOUT TIME ZONE` holding UTC
wall-clock values (the DAL writes `NOW()` on a UTC server). `UPDATED_AT_BEFORE`
is therefore a **naive UTC** `datetime` so the comparison lines up.

## What it does, per batch

1. `SELECT id, l1_batch_number, status, updated_at` for that batch, filtered by
   `status NOT IN (EXCLUDED_STATUSES)`, `updated_at < UPDATED_AT_BEFORE`,
   `chain_id = CHAIN_ID` (all constants).
2. **No rows** → log the batch as not found and skip it.
3. **Query error** → wait and retry (3 times); if it still fails, log the
   failing batch and stop the loop.
4. **Validate every returned row** — `id` is a positive integer and unique,
   `l1_batch_number` equals the requested batch, `status` is not a protected
   status, `updated_at` is non-NULL and before the cutoff. Any
   mismatch aborts without deleting anything. On success it prints an `ok:` line
   with the row count, status breakdown, and `updated_at` span.
5. Build `DELETE FROM prover_jobs_fri WHERE id IN (<ids>)`. `id` is the primary
   key, so the id list alone fixes exactly which rows go.
6. Dry run → print `dry run, delete statement to be executed: <statement>`.
7. Live → print `executing the statement, <statement>`, then run it. The DELETE
   is committed only if its row count matches the number of validated ids;
   otherwise it is rolled back and the run aborts. Failures retry 3 times, then
   the failing batch is logged and the loop stops.

A closing summary reports batches processed, batches skipped, rows deleted (or
matched, in dry run), and the failing batch if the run aborted.

**Exit codes:** `0` success, `1` aborted at some batch, `2` bad configuration.

The job is safe to re-run: rows deleted by an earlier run simply no longer match,
so the batch is reported as "no rows found" and skipped.

## Running locally

```bash
pip install -r requirements.txt
DATABASE_PROVER_URL='postgres://user:pass@localhost:5432/prover_db' \
  START_BATCH_ID_INCLUSIVE=1000 \
  END_BATCH_ID_INCLUSIVE=1200 \
  IS_DRY_RUN=true \
  python3 remove_archivable_prover_jobs_fri.py
```

## Build and push

The batch range and dry-run flag are **not** baked into the image — they are
supplied as env vars by the Kubernetes Job — so one build serves every range. The
constants above *are* baked in, so changing one means a new image version.

```bash
./build.sh v1.0.0
```

```bash
./push.sh v1.0.0 testnet
```