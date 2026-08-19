#!/usr/bin/env python3
"""
Delete archivable rows from the prover DB's `prover_jobs_fri` table for a
contiguous, inclusive range of L1 batch numbers.

"Archivable" mirrors `FriProverDal::archive_old_jobs`
(prover/crates/lib/prover_dal/src/fri_prover_dal.rs): a row is eligible when its
status is terminal (NOT queued / in_progress / in_gpu_proof / failed) and it has
not been touched since a cutoff timestamp. Unlike `archive_old_jobs` this script
does NOT copy rows into `prover_jobs_fri_archive` — the rows are dropped.

Only four things vary per run and they come from the environment: the DB URL, the
batch range, and the dry-run flag (see README.md). Everything else — chain id,
cutoff, protected statuses, retry policy — is fixed in the CONSTANTS block below,
so a deployment cannot get them subtly wrong. Change them here and cut a new
image version if they ever need to move.

One batch is handled at a time: SELECT the candidate rows, validate every
returned row against what was asked for, then DELETE by primary key. A batch with
no candidate rows is logged and skipped. Transient DB errors are retried; if a
batch still fails, the loop stops so the failure cannot be silently skipped over.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

# --- CONSTANTS: fixed run behaviour, deliberately not configurable ------------

# `prover_jobs_fri.chain_id` to operate on.
CHAIN_ID = 325

# Only rows strictly older than this are eligible. `prover_jobs_fri.updated_at`
# is `TIMESTAMP WITHOUT TIME ZONE` holding UTC wall-clock values (the DAL writes
# `NOW()` on a UTC server), so this is a naive UTC datetime to match.
# 2026-08-15 00:00:00 UTC.
UPDATED_AT_BEFORE = datetime(2026, 8, 15, 0, 0, 0)

# Statuses that mean a job is still live and must never be deleted. Kept in sync
# with the `archive_old_jobs` query in fri_prover_dal.rs.
EXCLUDED_STATUSES = ("queued", "in_progress", "in_gpu_proof", "failed")

# Retries per failed SELECT/DELETE, the wait between them, and the per-statement
# Postgres timeout.
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
STATEMENT_TIMEOUT_SECONDS = 300

# -----------------------------------------------------------------------------

TABLE = sql.Identifier("prover_jobs_fri")


def log(message: str = "") -> None:
    """stdout, unbuffered — container logs should stay in causal order."""
    print(message, flush=True)


class ConfigError(RuntimeError):
    pass


class BatchAborted(RuntimeError):
    """A batch could not be completed; the run must stop here."""


def env_str(name: str, default: str | None = None) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        if default is None:
            raise ConfigError(f"{name} must be set")
        return default
    return value.strip()


def env_int(name: str) -> int:
    raw = env_str(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def env_bool(name: str, default: str) -> bool:
    raw = env_str(name, default).lower()
    if raw in ("1", "true", "t", "yes", "y", "on"):
        return True
    if raw in ("0", "false", "f", "no", "n", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


@dataclass
class Config:
    # From the environment.
    dsn: str
    start_batch_id_inclusive: int
    end_batch_id_inclusive: int
    is_dry_run: bool
    # Fixed; carried on the config so call sites read from one place.
    chain_id: int = CHAIN_ID
    updated_at_before: datetime = UPDATED_AT_BEFORE
    excluded_statuses: list[str] = field(default_factory=lambda: list(EXCLUDED_STATUSES))
    max_retries: int = MAX_RETRIES
    retry_delay_seconds: float = RETRY_DELAY_SECONDS
    statement_timeout_seconds: int = STATEMENT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "Config":
        dsn = os.environ.get("DATABASE_PROVER_URL", "").strip()
        if not dsn:
            raise ConfigError(
                "DATABASE_PROVER_URL must be set "
                "(e.g. postgres://user:pass@host:5432/prover_db)"
            )

        start = env_int("START_BATCH_ID_INCLUSIVE")
        end = env_int("END_BATCH_ID_INCLUSIVE")
        if start > end:
            raise ConfigError(
                "START_BATCH_ID_INCLUSIVE must be <= END_BATCH_ID_INCLUSIVE "
                f"(got {start} > {end})"
            )
        if start < 0:
            raise ConfigError(f"START_BATCH_ID_INCLUSIVE must be >= 0, got {start}")

        return cls(
            dsn=dsn,
            start_batch_id_inclusive=start,
            end_batch_id_inclusive=end,
            is_dry_run=env_bool("IS_DRY_RUN", "true"),
        )

    @property
    def max_attempts(self) -> int:
        return self.max_retries + 1

    @property
    def batch_numbers(self) -> range:
        return range(self.start_batch_id_inclusive, self.end_batch_id_inclusive + 1)

    def print_summary(self) -> None:
        """Print the effective config. Never prints the DSN itself."""
        # conninfo_to_dict understands both URLs and key=value DSNs; only the
        # non-secret coordinates are echoed (never user/password).
        try:
            info = conninfo_to_dict(self.dsn)
        except Exception:  # noqa: BLE001 - malformed DSN is reported on connect
            info = {}

        log("=== configuration (from env) ===")
        log(f"  prover db host            : {info.get('host', '<unset>')}")
        log(f"  prover db port            : {info.get('port', '<default>')}")
        log(f"  prover db name            : {info.get('dbname', '<unset>')}")
        log("  prover db credentials     : <redacted>")
        log(f"  START_BATCH_ID_INCLUSIVE  : {self.start_batch_id_inclusive}")
        log(f"  END_BATCH_ID_INCLUSIVE    : {self.end_batch_id_inclusive}")
        log(f"  batches in range          : {len(self.batch_numbers)}")
        log(f"  IS_DRY_RUN                : {self.is_dry_run}")
        log("=== fixed behaviour (constants) ===")
        log(f"  chain_id                  : {self.chain_id}")
        log(f"  updated_at before (UTC)   : {self.updated_at_before.isoformat()}")
        log(f"  protected statuses        : {','.join(self.excluded_statuses)}")
        log(f"  max retries               : {self.max_retries} "
            f"({self.max_attempts} attempts per operation)")
        log(f"  retry delay seconds       : {self.retry_delay_seconds}")
        log(f"  statement timeout seconds : {self.statement_timeout_seconds}")
        log("===================================")


@dataclass
class Row:
    id: int
    batch_number: int
    status: str
    updated_at: datetime | None


@dataclass
class Summary:
    total_batches: int
    dry_run: bool
    batches_with_rows: list[int] = field(default_factory=list)
    batches_not_found: list[int] = field(default_factory=list)
    rows_deleted: int = 0
    failed_batch: int | None = None
    failure_reason: str | None = None

    @property
    def processed(self) -> int:
        return len(self.batches_with_rows) + len(self.batches_not_found)

    def print_report(self) -> None:
        rows_label = "rows matched (dry run)" if self.dry_run else "rows deleted"
        log()
        log("=== summary ===")
        log(f"  batches in range          : {self.total_batches}")
        log(f"  batches processed         : {self.processed}")
        log(f"  batches with matching rows: {len(self.batches_with_rows)}")
        log(f"  batches with no rows      : {len(self.batches_not_found)}")
        log(f"  {rows_label:<26}: {self.rows_deleted}")
        if self.batches_not_found:
            log(f"  skipped (no rows) batches : {format_batch_list(self.batches_not_found)}")
        if self.failed_batch is not None:
            not_attempted = self.total_batches - self.processed
            log(f"  FAILED at batch           : {self.failed_batch}")
            log(f"  failure reason            : {self.failure_reason}")
            log(f"  batches not attempted     : {not_attempted}")
            log("  result                    : ABORTED")
        else:
            log("  result                    : OK")
        log("===============")


def format_batch_list(batches: list[int], limit: int = 40) -> str:
    if len(batches) <= limit:
        return ", ".join(str(b) for b in batches)
    head = ", ".join(str(b) for b in batches[:limit])
    return f"{head}, ... (+{len(batches) - limit} more)"


class Db:
    """A lazily-(re)connected psycopg connection with explicit commits."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._conn: psycopg.Connection | None = None

    def connect(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._config.dsn, autocommit=False)
            with self._conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SET statement_timeout = {}").format(
                        sql.Literal(self._config.statement_timeout_seconds * 1000)
                    )
                )
            self._conn.commit()
        return self._conn

    def recover(self) -> None:
        """Make the connection usable again after a failed operation."""
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        if not conn.closed and not conn.broken:
            try:
                conn.rollback()
                self._conn = conn
                return
            except psycopg.Error:
                pass
        try:
            conn.close()
        except psycopg.Error:
            pass

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            try:
                self._conn.rollback()
            except psycopg.Error:
                pass
            self._conn.close()
        self._conn = None


def with_retries(config: Config, label: str, operation):
    """
    Run `operation()`, retrying transient DB errors up to MAX_RETRIES times.

    Raises BatchAborted when every attempt fails.
    """
    last_error: Exception | None = None
    for attempt in range(1, config.max_attempts + 1):
        try:
            return operation()
        except psycopg.Error as exc:
            last_error = exc
            log(f"  !! {label} failed on attempt {attempt}/{config.max_attempts}: "
                f"{type(exc).__name__}: {str(exc).strip()}")
            if attempt < config.max_attempts:
                log(f"     waiting {config.retry_delay_seconds}s before retry...")
                time.sleep(config.retry_delay_seconds)
    raise BatchAborted(f"{label} failed after {config.max_attempts} attempts: {last_error}")


def select_candidates(db: Db, config: Config, batch_number: int) -> list[Row]:
    # The protected statuses are rendered as SQL literals rather than bound as a
    # parameter, so the predicate reads exactly like `archive_old_jobs` in
    # fri_prover_dal.rs. They come from the EXCLUDED_STATUSES constant, and
    # sql.Literal quotes them, so nothing user-supplied reaches the SQL text.
    query = sql.SQL(
        """
        SELECT
            p.id,
            p.l1_batch_number,
            p.status,
            p.updated_at
        FROM {table} AS p
        WHERE
            p.status NOT IN ({excluded_statuses})
            AND p.updated_at < %(updated_at_before)s
            AND p.chain_id = %(chain_id)s
            AND p.l1_batch_number = %(l1_batch_number)s
        ORDER BY p.id
        """
    ).format(
        table=TABLE,
        excluded_statuses=sql.SQL(", ").join(
            sql.Literal(status) for status in config.excluded_statuses
        ),
    )
    params = {
        "updated_at_before": config.updated_at_before,
        "chain_id": config.chain_id,
        "l1_batch_number": batch_number,
    }

    def run() -> list[Row]:
        conn = db.connect()
        with conn.cursor() as cur:
            cur.execute(query, params)
            fetched = cur.fetchall()
        conn.commit()
        return [Row(id=r[0], batch_number=r[1], status=r[2], updated_at=r[3]) for r in fetched]

    try:
        return with_retries(config, f"SELECT for batch {batch_number}", run)
    except BatchAborted:
        db.recover()
        raise


def validate_rows(config: Config, batch_number: int, rows: list[Row]) -> None:
    """
    Assert every returned row is what was asked for, so a mis-built query can
    never feed unexpected ids into the DELETE. Any problem aborts the run.
    """
    problems: list[str] = []
    seen_ids: set[int] = set()

    for row in rows:
        if not isinstance(row.id, int) or row.id <= 0:
            problems.append(f"row with invalid id {row.id!r}")
            continue
        if row.id in seen_ids:
            problems.append(f"id {row.id} returned more than once")
        seen_ids.add(row.id)

        if row.batch_number != batch_number:
            problems.append(
                f"id {row.id}: l1_batch_number {row.batch_number} != requested {batch_number}"
            )
        if not isinstance(row.status, str) or not row.status:
            problems.append(f"id {row.id}: unusable status {row.status!r}")
        elif row.status in config.excluded_statuses:
            problems.append(
                f"id {row.id}: status {row.status!r} is a protected status "
                "and must not be deleted"
            )
        if row.updated_at is None:
            problems.append(f"id {row.id}: updated_at is NULL")
        elif row.updated_at >= config.updated_at_before:
            problems.append(
                f"id {row.id}: updated_at {row.updated_at.isoformat()} is not before "
                f"cutoff {config.updated_at_before.isoformat()}"
            )

    if problems:
        for problem in problems[:20]:
            log(f"  !! unexpected row: {problem}")
        if len(problems) > 20:
            log(f"  !! ... and {len(problems) - 20} more problems")
        raise BatchAborted(
            f"batch {batch_number}: {len(problems)} row(s) failed validation"
        )


def describe_rows(rows: list[Row]) -> str:
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
    return ", ".join(f"{status}={count}" for status, count in sorted(by_status.items()))


def build_delete_statement(db: Db, ids: list[int]) -> str:
    """
    Render `DELETE FROM prover_jobs_fri WHERE id IN (...)` as literal SQL.

    `id` is the table's primary key, so the id list alone fully determines the
    rows removed. The statement is rendered (not parameterised) so the exact SQL
    that runs is what gets printed in both dry-run and live mode.
    """
    statement = sql.SQL("DELETE FROM {table} WHERE id IN ({ids})").format(
        table=TABLE,
        ids=sql.SQL(", ").join(sql.Literal(i) for i in ids),
    )
    return statement.as_string(db.connect())


def execute_delete(db: Db, config: Config, batch_number: int, statement: str, expected: int) -> int:
    def run() -> int:
        conn = db.connect()
        with conn.cursor() as cur:
            cur.execute(statement)
            affected = cur.rowcount
        if affected != expected:
            # Rows vanished or multiplied between SELECT and DELETE. Roll back
            # and stop rather than guess.
            conn.rollback()
            raise BatchAborted(
                f"batch {batch_number}: DELETE affected {affected} row(s), expected "
                f"{expected}; rolled back"
            )
        conn.commit()
        return affected

    try:
        return with_retries(config, f"DELETE for batch {batch_number}", run)
    except BatchAborted:
        db.recover()
        raise


def process_batch(db: Db, config: Config, batch_number: int, summary: Summary) -> None:
    log(f"-- batch {batch_number}")
    rows = select_candidates(db, config, batch_number)

    if not rows:
        log(f"  batch {batch_number}: no archivable rows found in prover_jobs_fri "
            f"(chain_id={config.chain_id}); skipping")
        summary.batches_not_found.append(batch_number)
        return

    validate_rows(config, batch_number, rows)
    oldest = min(r.updated_at for r in rows)
    newest = max(r.updated_at for r in rows)
    log(f"  ok: batch {batch_number} — {len(rows)} row(s) validated "
        f"(all l1_batch_number={batch_number}, chain_id={config.chain_id}, "
        f"statuses [{describe_rows(rows)}], updated_at "
        f"{oldest.isoformat()}..{newest.isoformat()} < {config.updated_at_before.isoformat()})")

    ids = [row.id for row in rows]
    statement = build_delete_statement(db, ids)

    if config.is_dry_run:
        log(f"  dry run, delete statement to be executed: {statement}")
        summary.batches_with_rows.append(batch_number)
        summary.rows_deleted += len(ids)
        return

    log(f"  executing the statement, {statement}")
    affected = execute_delete(db, config, batch_number, statement, len(ids))
    log(f"  deleted {affected} row(s) for batch {batch_number}")
    summary.batches_with_rows.append(batch_number)
    summary.rows_deleted += affected


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        log(f"!! configuration error: {exc}")
        return 2

    config.print_summary()
    mode = "DRY RUN — no rows will be deleted" if config.is_dry_run else "LIVE — rows will be deleted"
    log(f"starting... ({mode})")

    summary = Summary(total_batches=len(config.batch_numbers), dry_run=config.is_dry_run)
    db = Db(config)

    try:
        for batch_number in config.batch_numbers:
            try:
                process_batch(db, config, batch_number, summary)
            except BatchAborted as exc:
                log(f"!! aborting: {exc}")
                summary.failed_batch = batch_number
                summary.failure_reason = str(exc)
                break
    finally:
        db.close()

    summary.print_report()
    return 1 if summary.failed_batch is not None else 0


if __name__ == "__main__":
    sys.exit(main())
