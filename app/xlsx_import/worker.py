"""Dedicated database-backed worker for XLSX imports.

Run from the backend directory with: python -m app.xlsx_import.worker
"""

import json
import logging
import os
import re
import time
from typing import List

from app.db import engine

from .control import fail_import, init_control_tables, set_status, update_progress
from .importer import (
    ImportResult,
    ImportValidationError,
    activate_dataset,
    build_indexes,
    create_import_table,
    load_rows_into_table,
    read_header,
    validate_import,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = max(1, int(os.environ.get("XLSX_BATCH_SIZE", "5000")))
MAX_BATCH_BYTES = int(os.environ.get("XLSX_MAX_BATCH_BYTES", str(8 * 1024 * 1024)))
PROGRESS_INTERVAL = max(1, int(os.environ.get("XLSX_PROGRESS_INTERVAL", "50000")))
POLL_SECONDS = max(0.1, float(os.environ.get("XLSX_WORKER_POLL_SECONDS", "5")))
WORKER_LOCK_NAME = "xlsx_import_worker"
TABLE_NAME_PATTERN = re.compile(r"^dataset_[0-9]+(?:_[a-f0-9]{8})?$")


def _drop_import_table(table_name: str):
    if not TABLE_NAME_PATTERN.fullmatch(table_name):
        raise ValueError(f"Invalid import table name: {table_name}")
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _recover_interrupted_jobs():
    """Requeue jobs left mid-import by a worker process that exited unexpectedly."""
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name, status, import_payload FROM dataset_versions "
            "WHERE status IN ('importing', 'loading', 'indexing')"
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    for table_name, status, payload in rows:
        if not payload:
            _drop_import_table(table_name)
            fail_import(
                table_name,
                "This import was started before the worker queue was enabled and "
                "cannot be resumed. Upload the file again.",
            )
            logger.warning("Marked legacy interrupted import %s as failed", table_name)
            continue

        _drop_import_table(table_name)
        conn = engine.raw_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE dataset_versions SET status = 'queued', row_count = 0, "
                "processed_rows = 0, error_message = NULL "
                "WHERE table_name = %s AND status = %s",
                (table_name, status),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
        logger.warning("Requeued interrupted import %s", table_name)


def _claim_next_job():
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name, import_payload FROM dataset_versions "
            "WHERE status = 'queued' AND import_payload IS NOT NULL "
            "ORDER BY created_at ASC LIMIT 1 FOR UPDATE"
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return None

        table_name, payload = row
        cur.execute(
            "UPDATE dataset_versions SET status = 'importing', error_message = NULL "
            "WHERE table_name = %s AND status = 'queued'",
            (table_name,),
        )
        if cur.rowcount != 1:
            conn.rollback()
            cur.close()
            return None
        conn.commit()
        cur.close()
        return table_name, payload
    finally:
        conn.close()


def _fail_and_drop(table_name: str, error_message: str):
    try:
        fail_import(table_name, error_message)
    finally:
        _drop_import_table(table_name)


def _run_import_job(table_name: str, payload: str):
    staged_paths: List[str] = []
    try:
        if not TABLE_NAME_PATTERN.fullmatch(table_name):
            raise ValueError(f"Invalid import table name: {table_name}")

        staged_paths = json.loads(payload)
        if not isinstance(staged_paths, list) or not staged_paths:
            raise ValueError("Import job has no staged files.")
        if not all(isinstance(path, str) for path in staged_paths):
            raise ValueError("Import job contains an invalid staged-file path.")
        missing_paths = [path for path in staged_paths if not os.path.isfile(path)]
        if missing_paths:
            raise FileNotFoundError(
                "Staged upload file(s) are missing. Ensure the API and worker share "
                f"the upload directory: {', '.join(missing_paths)}"
            )

        logger.info("Starting import job %s with %d file(s)", table_name, len(staged_paths))
        columns = read_header(staged_paths[0])
        create_import_table(table_name, columns)
        update_progress(table_name, 0)
        row_count = load_rows_into_table(
            table_name,
            columns,
            staged_paths,
            batch_size=BATCH_SIZE,
            max_batch_bytes=MAX_BATCH_BYTES,
            progress_cb=lambda count: update_progress(table_name, count),
            status_cb=lambda status: set_status(table_name, status),
            progress_interval=PROGRESS_INTERVAL,
        )

        validate_import(
            ImportResult(table_name=table_name, row_count=row_count, columns=columns)
        )
        set_status(table_name, "indexing", row_count=row_count)
        build_indexes(table_name)
        activate_dataset(table_name)
        logger.info("Import job %s activated with %d rows", table_name, row_count)
    except ImportValidationError as exc:
        logger.exception("Import validation failed for %s", table_name)
        _fail_and_drop(table_name, str(exc))
    except Exception as exc:
        logger.exception("Import failed for %s", table_name)
        _fail_and_drop(table_name, str(exc))
    finally:
        for path in staged_paths:
            if os.path.exists(path):
                os.remove(path)
        logger.info("Import job finished for %s", table_name)


def _acquire_worker_lock():
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("SET SESSION wait_timeout = 86400")
        cur.execute("SELECT GET_LOCK(%s, 0)", (WORKER_LOCK_NAME,))
        acquired = cur.fetchone()[0] == 1
        cur.close()
        if not acquired:
            raise RuntimeError("Another XLSX import worker already holds the database lock.")
        return conn
    except Exception:
        conn.close()
        raise


def run_worker():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_control_tables()
    lock_conn = _acquire_worker_lock()
    logger.info("XLSX import worker started; polling every %s seconds", POLL_SECONDS)
    try:
        _recover_interrupted_jobs()
        while True:
            job = _claim_next_job()
            if job is None:
                time.sleep(POLL_SECONDS)
                continue
            _run_import_job(*job)
    finally:
        try:
            cur = lock_conn.cursor()
            cur.execute("SELECT RELEASE_LOCK(%s)", (WORKER_LOCK_NAME,))
            cur.close()
        finally:
            lock_conn.close()


if __name__ == "__main__":
    run_worker()
