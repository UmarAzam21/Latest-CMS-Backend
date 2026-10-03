import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import require_super_admin
from ..db import Base, get_db


_default_block_file = Path(__file__).resolve().parents[2] / ".backend.blocked"
BLOCK_FILE = Path(os.getenv("BACKEND_BLOCK_FILE", str(_default_block_file))).expanduser()

router = APIRouter(prefix="/api/emergency", tags=["Emergency"])


def is_backend_blocked() -> bool:
	return BLOCK_FILE.is_file()


def clean_database(db: Session) -> int:
	"""Delete all rows from application tables in foreign-key-safe order."""
	deleted_count = 0
	for table in reversed(Base.metadata.sorted_tables):
		deleted_count += db.execute(table.delete()).rowcount or 0
	if db.bind.dialect.has_table(db.connection(), "dataset_versions"):
		deleted_count += db.execute(text("DELETE FROM dataset_versions")).rowcount or 0
	return deleted_count


@router.post("/clean-and-block", status_code=status.HTTP_200_OK)
def clean_and_block_backend(
	db: Session = Depends(get_db),
	_current_admin: dict = Depends(require_super_admin),
):
	if is_backend_blocked():
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Backend is blocked.",
		)

	try:
		deleted_count = clean_database(db)
		db.commit()
		BLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
		BLOCK_FILE.write_text("Backend blocked by emergency cleanup.\n", encoding="utf-8")
	except Exception:
		db.rollback()
		raise

	return {
		"status": "blocked",
		"message": "Database cleaned and backend blocked.",
		"deleted_rows": deleted_count,
	}
