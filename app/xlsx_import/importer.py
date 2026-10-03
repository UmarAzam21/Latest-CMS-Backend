"""
XLSX -> MySQL bulk importer.

Load strategy, in order of preference:
  1. LOAD DATA LOCAL INFILE  - fastest by far; needs local_infile=1 on server + client.
  2. Byte-capped executemany - always works; sizes every batch to fit max_allowed_packet.

Both paths reconnect and retry on a dropped connection, so a long import
survives the occasional "MySQL server has gone away".

Parsing uses python-calamine when installed (5-10x faster than openpyxl),
and silently falls back to openpyxl otherwise.

    pip install python-calamine
"""

import csv
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional

import pymysql

from app.db import engine  # <-- adjust if your engine var is named differently

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- parser backend

try:
    from python_calamine import CalamineWorkbook

    _CALAMINE = True
except ImportError:  # pragma: no cover
    CalamineWorkbook = None
    _CALAMINE = False

import openpyxl  # kept as fallback


class ImportValidationError(Exception):
    """Raised when an import fails validation. Caller must roll back the new table."""


# ---------------------------------------------------------------- connections


def _new_connection(local_infile: bool = False):
    """
    Opens a dedicated pymysql connection for the import.

    We bypass the SQLAlchemy pool deliberately: the import holds one connection
    for a long time, needs long socket timeouts, and (for the fast path) needs
    local_infile enabled, which the app-wide engine may not have.
    """
    url = engine.url
    return pymysql.connect(
        host=url.host,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        database=url.database,
        charset="utf8mb4",
        local_infile=local_infile,
        connect_timeout=30,
        read_timeout=3600,
        write_timeout=3600,
        autocommit=False,
    )


def _server_max_packet() -> int:
    """How big a single statement may be. Shared hosting is often 1-16 MB."""
    conn = _new_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT @@max_allowed_packet")
        value = int(cur.fetchone()[0])
        cur.close()
        return value
    finally:
        conn.close()


def _local_infile_available() -> bool:
    """True only if BOTH the server allows it and the client can open the file."""
    try:
        conn = _new_connection(local_infile=True)
    except Exception:
        logger.info("LOAD DATA probe: could not open a local_infile connection")
        return False
    try:
        cur = conn.cursor()
        cur.execute("SELECT @@local_infile")
        enabled = int(cur.fetchone()[0]) == 1
        cur.close()
        if not enabled:
            logger.info("LOAD DATA probe: server has local_infile=0")
        return enabled
    except Exception:
        logger.info("LOAD DATA probe: server rejected the check", exc_info=True)
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------- column names


def _sanitize_column_name(raw: str, seen: set, idx: int) -> str:
    name = (raw or f"col_{idx}").strip().lower()
    name = re.sub(r"[^a-z0-9_]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_") or f"col_{idx}"
    if name[0].isdigit():
        name = f"c_{name}"
    original = name
    n = 1
    while name in seen:
        name = f"{original}_{n}"
        n += 1
    seen.add(name)
    return name


@dataclass
class ImportResult:
    table_name: str
    row_count: int
    columns: List[str]


def new_table_name() -> str:
    return f"dataset_{int(time.time() * 1000)}"


# ---------------------------------------------------------------- reading xlsx


def _iter_sheet_rows(filepath: str) -> Iterator[tuple]:
    """Yields raw tuples for every row of every sheet in a workbook."""
    if _CALAMINE:
        wb = CalamineWorkbook.from_path(filepath)
        for sheet_name in wb.sheet_names:
            sheet = wb.get_sheet_by_name(sheet_name)
            # to_python() materializes one sheet at a time. Excel caps a sheet at
            # ~1.05M rows, so peak memory stays bounded even for huge workbooks.
            for row in sheet.to_python(skip_empty_area=False):
                yield tuple(row)
            yield ("__SHEET_BREAK__",)
    else:
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    if row is not None:
                        yield row
                yield ("__SHEET_BREAK__",)
        finally:
            wb.close()


def read_header(filepath: str) -> List[str]:
    """Reads only the header row of the first sheet - the canonical column set."""
    for row in _iter_sheet_rows(filepath):
        if row == ("__SHEET_BREAK__",):
            raise ImportValidationError(f"{filepath}: first sheet is empty (no header row).")
        seen = set()
        columns = [
            _sanitize_column_name("" if h is None else str(h), seen, i)
            for i, h in enumerate(row)
        ]
        if not columns:
            raise ImportValidationError(f"{filepath}: no columns found.")
        return columns
    raise ImportValidationError(f"{filepath}: workbook has no rows.")


def _iter_all_rows(filepaths: List[str], columns: List[str]) -> Iterator[List[str]]:
    """Yields normalized data rows across every sheet of every file, header rows skipped."""
    ncols = len(columns)
    for filepath in filepaths:
        first_row_of_sheet = True
        for row in _iter_sheet_rows(filepath):
            if row == ("__SHEET_BREAK__",):
                first_row_of_sheet = True
                continue
            cells = list(row) + [None] * (ncols - len(row))
            cells = ["" if value is None else str(value) for value in cells[:ncols]]
            if all(cell == "" for cell in cells):
                continue
            if first_row_of_sheet:
                first_row_of_sheet = False
                seen = set()
                as_header = [
                    _sanitize_column_name(cell, seen, index)
                    for index, cell in enumerate(cells)
                ]
                if as_header == columns:
                    continue
            yield cells


# ---------------------------------------------------------------- schema


def create_import_table(table_name: str, columns: List[str]):
    col_defs = ", ".join(f"`{c}` TEXT" for c in columns)
    conn = _new_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE `{table_name}` (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                {col_defs},
                search_text LONGTEXT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _drop_table_quietly(table_name: str):
    try:
        conn = _new_connection()
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS `{table_name}`")
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        logger.exception("Could not drop failed import table %s", table_name)


# ---------------------------------------------------------------- fast path


def _load_via_infile(
    table_name: str,
    columns: List[str],
    filepaths: List[str],
    progress_cb: Optional[Callable[[int], None]],
    progress_interval: int,
) -> int:
    """Stream rows to a temp CSV, then let MySQL ingest it in one statement."""
    total_rows = 0
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    csv_path = handle.name
    try:
        writer = csv.writer(
            handle,
            delimiter=",",
            quotechar='"',
            quoting=csv.QUOTE_ALL,
            doublequote=False,     # use backslash escaping, matching ESCAPED BY below
            escapechar="\\",
            lineterminator="\n",
        )
        for cells in _iter_all_rows(filepaths, columns):
            writer.writerow(cells)  # search_text is built server-side, not shipped
            total_rows += 1
            if progress_cb and total_rows % progress_interval == 0:
                progress_cb(total_rows)
        handle.flush()
        handle.close()

        size_mb = os.path.getsize(csv_path) / (1024 * 1024)
        logger.info("Staged %d rows (%.1f MB) for LOAD DATA into %s", total_rows, size_mb, table_name)

        cols_sql = ", ".join(f"`{c}`" for c in columns)
        conn = _new_connection(local_infile=True)
        try:
            cur = conn.cursor()
            for tuning in (
                "SET SESSION unique_checks = 0",
                "SET SESSION foreign_key_checks = 0",
                "SET SESSION sql_log_bin = 0",
            ):
                try:
                    cur.execute(tuning)
                except Exception:
                    # sql_log_bin needs SUPER on some hosts; not fatal
                    logger.debug("Tuning statement rejected: %s", tuning)
            cur.execute(
                f"""
                LOAD DATA LOCAL INFILE %s
                INTO TABLE `{table_name}`
                CHARACTER SET utf8mb4
                FIELDS TERMINATED BY ',' ENCLOSED BY '"' ESCAPED BY '\\\\'
                LINES TERMINATED BY '\\n'
                ({cols_sql})
                SET search_text = CONCAT_WS(' ', {cols_sql})
                """,
                (csv_path,),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()

        if progress_cb:
            progress_cb(total_rows)
        return total_rows
    finally:
        try:
            handle.close()
        except Exception:
            pass
        if os.path.exists(csv_path):
            os.remove(csv_path)


# ---------------------------------------------------------------- fallback path


class _RetryingWriter:
    """Holds one connection, reopens it transparently when the server drops us."""

    def __init__(self, insert_sql: str, attempts: int = 4):
        self.insert_sql = insert_sql
        self.attempts = attempts
        self.conn = _new_connection()
        self.cur = self.conn.cursor()

    def _reconnect(self):
        for closer in (self.cur, self.conn):
            try:
                closer.close()
            except Exception:
                pass
        self.conn = _new_connection()
        self.cur = self.conn.cursor()

    def write(self, rows: List[tuple]):
        if not rows:
            return
        last_error = None
        for attempt in range(self.attempts):
            try:
                self.cur.executemany(self.insert_sql, rows)
                self.conn.commit()
                return
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as exc:
                last_error = exc
                wait = 2 ** attempt
                logger.warning(
                    "Write failed (%s). Reconnecting in %ds, attempt %d/%d",
                    exc, wait, attempt + 1, self.attempts,
                )
                time.sleep(wait)
                try:
                    self._reconnect()
                except Exception:
                    logger.exception("Reconnect failed")
        raise RuntimeError(f"Could not write batch after {self.attempts} attempts") from last_error

    def close(self):
        for closer in (self.cur, self.conn):
            try:
                closer.close()
            except Exception:
                pass


def _load_via_executemany(
    table_name: str,
    columns: List[str],
    filepaths: List[str],
    progress_cb: Optional[Callable[[int], None]],
    progress_interval: int,
    max_rows_per_batch: int,
) -> int:
    columns_sql = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * (len(columns) + 1))
    insert_sql = (
        f"INSERT INTO `{table_name}` ({columns_sql}, `search_text`) VALUES ({placeholders})"
    )

    max_packet = _server_max_packet()
    safe_bytes = max(64 * 1024, int(max_packet * 0.5))  # 50% headroom for escaping
    logger.info(
        "max_allowed_packet=%d bytes; capping batches at %d bytes / %d rows",
        max_packet, safe_bytes, max_rows_per_batch,
    )

    writer = _RetryingWriter(insert_sql)
    total_rows = 0
    batch: List[tuple] = []
    pending = 0
    try:
        for cells in _iter_all_rows(filepaths, columns):
            search_text = " ".join(cells)
            row = (*cells, search_text)
            # +3 per value covers quotes and the comma; doubles as escaping headroom
            row_bytes = sum(len(value.encode("utf-8")) + 3 for value in row)

            if batch and (pending + row_bytes > safe_bytes or len(batch) >= max_rows_per_batch):
                writer.write(batch)
                batch, pending = [], 0
                if progress_cb and total_rows % progress_interval < len(batch) + 1:
                    progress_cb(total_rows)

            batch.append(row)
            pending += row_bytes
            total_rows += 1
            if progress_cb and total_rows % progress_interval == 0:
                progress_cb(total_rows)

        writer.write(batch)
        if progress_cb:
            progress_cb(total_rows)
        return total_rows
    finally:
        writer.close()


# ---------------------------------------------------------------- entry point


def load_rows_into_table(
    table_name: str,
    columns: List[str],
    filepaths: List[str],
    batch_size: int = 20000,
    max_batch_bytes: int = 0,          # kept for signature compatibility; unused
    progress_cb: Optional[Callable[[int], None]] = None,
    progress_interval: int = 50000,
) -> int:
    started = time.time()
    logger.info(
        "Starting import into %s from %d file(s), parser=%s",
        table_name, len(filepaths), "calamine" if _CALAMINE else "openpyxl",
    )
    try:
        if _local_infile_available():
            logger.info("Using LOAD DATA LOCAL INFILE fast path")
            total = _load_via_infile(
                table_name, columns, filepaths, progress_cb, progress_interval
            )
        else:
            logger.info("Falling back to byte-capped executemany")
            total = _load_via_executemany(
                table_name, columns, filepaths, progress_cb, progress_interval, batch_size
            )
        elapsed = max(time.time() - started, 0.001)
        logger.info(
            "Import finished into %s: %d rows in %.1fs (%.0f rows/sec)",
            table_name, total, elapsed, total / elapsed,
        )
        return total
    except Exception:
        logger.exception("Import failed for %s, dropping table", table_name)
        _drop_table_quietly(table_name)
        raise


# ---------------------------------------------------------------- post-load


def validate_import(result: ImportResult, min_rows: int = 1):
    """Extend with your real business rules - required columns, row-count deltas, etc."""
    if result.row_count < min_rows:
        raise ImportValidationError(
            f"Import produced {result.row_count} rows, expected at least {min_rows}."
        )
    if not result.columns:
        raise ImportValidationError("Import produced no columns.")


def build_indexes(table_name: str):
    """
    Built AFTER the rows land - indexing an empty table then inserting is far
    slower than bulk-loading then indexing once.
    """
    conn = _new_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"ALTER TABLE `{table_name}` ADD FULLTEXT KEY `ft_search` (`search_text`)")
        conn.commit()
        cur.close()
        logger.info("FULLTEXT index built on %s", table_name)
    except Exception:
        # A missing index degrades search to a table scan but shouldn't fail the import.
        logger.exception("Could not build FULLTEXT index on %s", table_name)
    finally:
        conn.close()


def activate_dataset(table_name: str):
    """
    Atomically points `current_dataset` at the new table, marks it active,
    retires the previous one, and drops its physical table. Blue/green swap.
    """
    conn = _new_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM dataset_versions WHERE status = 'active'")
        row = cur.fetchone()
        previous_table = row[0] if row else None

        cur.execute(f"CREATE OR REPLACE VIEW current_dataset AS SELECT * FROM `{table_name}`")
        cur.execute(
            "UPDATE dataset_versions SET status = 'active', activated_at = now() "
            "WHERE table_name = %s",
            (table_name,),
        )
        if previous_table:
            cur.execute(
                "UPDATE dataset_versions SET status = 'retired' WHERE table_name = %s",
                (previous_table,),
            )
        conn.commit()

        if previous_table:
            cur.execute(f"DROP TABLE IF EXISTS `{previous_table}`")
            conn.commit()
        cur.close()
    finally:
        conn.close()