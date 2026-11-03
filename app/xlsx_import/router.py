import logging
import json
import os
import shutil
import tempfile
import uuid
from typing import List

from fastapi import APIRouter, UploadFile, File, HTTPException, Query

from app.db import engine  # <-- adjust if your engine var is named differently
from .importer import new_table_name

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.environ.get(
    "XLSX_UPLOAD_DIR",
    os.path.join(tempfile.gettempdir(), "xlsx-uploads"),
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter(prefix="/xlsx", tags=["xlsx-import"])


def queue_import(staged_paths: List[str], filenames: List[str]) -> str:
    table_name = new_table_name()

    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO dataset_versions "
            "(table_name, status, row_count, processed_rows, source_file, import_payload) "
            "VALUES (%s, 'queued', 0, 0, %s, %s)",
            (table_name, ", ".join(filenames), json.dumps(staged_paths)),
        )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        for path in staged_paths:
            if os.path.exists(path):
                os.remove(path)
        raise
    finally:
        conn.close()

    logger.info("Queued import %s with %d files", table_name, len(staged_paths))
    return table_name


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
    files: List[UploadFile] = File(..., description="One or more .xlsx or .xlsm files to import"),
):
    """
    Accepts one or more .xlsx/.xlsm files - useful when a dataset is split
    across files or sheets because Excel caps a single sheet at 1,048,576 rows.
    All files/sheets load into ONE new table in the worker; the request returns
    immediately with a table_name to poll via GET /xlsx/status/{table_name}.
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

    table_name = queue_import(staged_paths, filenames)

    return {
        "status": "queued",
        "table_name": table_name,
        "files": filenames,
        "message": "Import queued for the XLSX worker. Poll GET /xlsx/status/{table_name} for progress.",
    }


@router.get("/status/{table_name}")
def get_status(table_name: str):
    """Poll this while queued/running. Status moves queued -> importing -> loading -> indexing -> active (or failed)."""
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
            cur.execute(
                "SELECT COUNT(*) FROM current_dataset "
                "WHERE MATCH(search_text) AGAINST (%s IN BOOLEAN MODE)",
                (q,),
            )
            total_count = cur.fetchone()[0]

            cur.execute(
                "SELECT * FROM current_dataset "
                "WHERE MATCH(search_text) AGAINST (%s IN BOOLEAN MODE) "
                "LIMIT %s OFFSET %s",
                (q, limit, offset),
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
        except Exception as exc:
            conn.rollback()
            if "current_dataset" in str(exc):
                raise HTTPException(404, "No dataset has been imported yet.") from exc
            raise
        finally:
            cur.close()
    finally:
        conn.close()