import logging
import os
import shutil
import uuid
import threading
from typing import List

from fastapi import APIRouter, BackgroundTasks, UploadFile, File, HTTPException, Query
from psycopg2 import errors as pg_errors

from app.db import engine  # <-- adjust if your engine var is named differently
from .control import set_status, update_progress
from .importer import (
    ImportResult,
    ImportValidationError,
    activate_dataset,
    build_indexes,
    create_import_table,
    load_rows_into_table,
    new_table_name,
    read_header,
    validate_import,
)

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.environ.get("XLSX_UPLOAD_DIR", "/tmp/xlsx-uploads")
BATCH_SIZE = int(os.environ.get("XLSX_BATCH_SIZE", "20000"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter(prefix="/xlsx", tags=["xlsx-import"])


@router.post(
    "/import",
    summary="Upload XLSX file(s) for import",
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "format": "binary"
                                },
                                "description": "One or more .xlsx or .xlsm files to import"
                            }
                        },
                        "required": ["files"]
                    }
                }
            }
        }
    }
)
async def import_xlsx(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(..., description="One or more .xlsx or .xlsm files to import"),
):
    """
    Accepts one or more .xlsx/.xlsm files - useful when a dataset is split
    across files or sheets because Excel caps a single sheet at 1,048,576 rows.
    All files/sheets load into ONE new table in the background; the request
    returns immediately with a table_name to poll via GET /xlsx/status/{table_name}.
    The dataset only goes live (blue/green swap) once everything has loaded
    and validation passes.
    """
    if not files:
        raise HTTPException(400, "No files uploaded.")

    staged_paths = []
    filenames = []
    for f in files:
        if not f.filename:
            raise HTTPException(400, "One of the uploaded files has no filename.")
        if not f.filename.lower().endswith((".xlsx", ".xlsm")):
            raise HTTPException(400, f"{f.filename}: only .xlsx/.xlsm files are supported.")
        staged_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{f.filename}")
        with open(staged_path, "wb") as out:
            shutil.copyfileobj(f.file, out)
        staged_paths.append(staged_path)
        filenames.append(f.filename)

    table_name = new_table_name()

    conn = engine.raw_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dataset_versions (table_name, status, row_count, processed_rows, source_file) "
        "VALUES (%s, 'importing', 0, 0, %s)",
        (table_name, ", ".join(filenames)),
    )
    conn.commit()
    cur.close()
    conn.close()

    logger.info("Scheduled import task for %s with %d files", table_name, len(staged_paths))

    # Start import in a background thread to ensure it runs
    import_thread = threading.Thread(target=_run_import_job, args=(table_name, staged_paths), daemon=False)
    import_thread.start()

    return {
        "status": "processing",
        "table_name": table_name,
        "files": filenames,
        "message": "Import started in background. Poll GET /xlsx/status/{table_name} for progress.",
    }


def _run_import_job(table_name: str, staged_paths: List[str]):
    logger.info("Running import job for %s", table_name)
    try:
        logger.info("Starting to read header from %s", staged_paths[0])
        columns = read_header(staged_paths[0])
        logger.info("Read %d columns: %s", len(columns), columns)
        
        logger.info("Creating table %s", table_name)
        create_import_table(table_name, columns)
        logger.info("Table created successfully")

        logger.info("Loading rows from %d files", len(staged_paths))
        row_count = load_rows_into_table(
            table_name,
            columns,
            staged_paths,
            batch_size=BATCH_SIZE,
            progress_cb=lambda n: update_progress(table_name, n),
        )
        logger.info("Loaded %d rows into table", row_count)

        result = ImportResult(table_name=table_name, row_count=row_count, columns=columns)
        validate_import(result)
        logger.info("Import validation passed")

        set_status(table_name, "indexing", row_count=row_count)
        build_indexes(table_name)
        logger.info("Indexes built successfully")

        activate_dataset(table_name)  # sets status='active' + activated_at itself
        logger.info("Dataset activated successfully")

    except ImportValidationError as e:
        logger.exception("Import validation failed for %s", table_name)
        _fail_and_drop(table_name, str(e))
    except Exception as e:
        logger.exception("Unexpected import failure for %s: %s", table_name, str(e))
        _fail_and_drop(table_name, str(e))
    finally:
        for p in staged_paths:
            if os.path.exists(p):
                os.remove(p)
        logger.info("Import job finished for %s", table_name)


def _fail_and_drop(table_name: str, error_message: str):
    set_status(table_name, "failed", error_message=error_message)
    conn = engine.raw_connection()
    cur = conn.cursor()
    cur.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.commit()
    cur.close()
    conn.close()


@router.get("/status/{table_name}")
def get_status(table_name: str):
    """Poll this while an import runs. status moves: importing -> indexing -> active (or failed)."""
    conn = engine.raw_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name, status, row_count, processed_rows, source_file, error_message, created_at, activated_at "
        "FROM dataset_versions WHERE table_name = %s",
        (table_name,),
    )
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(404, "Unknown table_name.")
    return dict(zip(cols, row))


@router.get("/datasets")
def list_datasets():
    conn = engine.raw_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name, status, row_count, processed_rows, source_file, error_message, created_at, activated_at "
        "FROM dataset_versions ORDER BY created_at DESC LIMIT 50"
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 20, offset: int = 0):
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        try:
            # First, get total count of matching results for pagination
            cur.execute(
                """
                SELECT COUNT(*) as total_count
                FROM current_dataset
                WHERE search_vector @@ websearch_to_tsquery('english', %s)
                """,
                (q,),
            )
            total_count = cur.fetchone()[0]
            
            # Then fetch paginated results with better ranking
            cur.execute(
                """
                SELECT *, 
                       ts_rank(search_vector, websearch_to_tsquery('english', %s)) AS rank,
                       ts_rank_cd(search_vector, websearch_to_tsquery('english', %s)) AS rank_cd
                FROM current_dataset
                WHERE search_vector @@ websearch_to_tsquery('english', %s)
                ORDER BY rank_cd DESC, rank DESC
                LIMIT %s OFFSET %s
                """,
                (q, q, q, limit, offset),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            conn.commit()
            
            return {
                "query": q, 
                "total_count": total_count,
                "count": len(rows), 
                "limit": limit,
                "offset": offset,
                "results": rows
            }
        except pg_errors.UndefinedTable:
            conn.rollback()
            raise HTTPException(404, "No dataset has been imported yet.")
        except pg_errors.SyntaxError:
            # Fallback to simpler query if websearch_to_tsquery fails
            conn.rollback()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COUNT(*) as total_count
                FROM current_dataset
                WHERE search_vector @@ plainto_tsquery('english', %s)
                """,
                (q,),
            )
            total_count = cur.fetchone()[0]
            
            cur.execute(
                """
                SELECT *, 
                       ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
                FROM current_dataset
                WHERE search_vector @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s OFFSET %s
                """,
                (q, q, limit, offset),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            conn.commit()
            
            return {
                "query": q, 
                "total_count": total_count,
                "count": len(rows), 
                "limit": limit,
                "offset": offset,
                "results": rows
            }
        finally:
            cur.close()
    finally:
        conn.close()