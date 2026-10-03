from sqlalchemy import inspect, text

from app.db import engine


def init_control_tables():
    """Creates the metadata table that tracks every import and which one is live."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE IF NOT EXISTS dataset_versions (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                table_name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'importing',
                row_count BIGINT DEFAULT 0,
                processed_rows BIGINT DEFAULT 0,
                source_file TEXT,
                error_message TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                activated_at DATETIME
            )
            """
        )
        )
        columns = {column["name"] for column in inspect(engine).get_columns("dataset_versions")}
        if "processed_rows" not in columns:
            conn.execute(
                text("ALTER TABLE dataset_versions ADD COLUMN processed_rows BIGINT DEFAULT 0")
            )


def set_status(table_name: str, status: str, **fields):
    """Updates status plus any extra columns, e.g. set_status(t, 'failed', error_message='...')."""
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        set_parts = ["status = %s"]
        values = [status]
        for key, value in fields.items():
            set_parts.append(f"{key} = %s")
            values.append(value)
        values.append(table_name)
        cur.execute(
            f"UPDATE dataset_versions SET {', '.join(set_parts)} WHERE table_name = %s",
            values,
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def update_progress(table_name: str, processed_rows: int):
    """Called frequently during a big import so /xlsx/status shows live progress."""
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dataset_versions SET processed_rows = %s, row_count = %s WHERE table_name = %s",
            (processed_rows, processed_rows, table_name),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()