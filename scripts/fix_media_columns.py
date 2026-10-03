import os

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://root:@localhost:3306/filernow_db",
)
engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='media'"))
    cols = {row[0] for row in result}
    print("existing columns:", sorted(cols))

    statements = []
    if "public_id" not in cols:
        statements.append("ALTER TABLE media ADD COLUMN public_id VARCHAR(500)")
    if "resource_type" not in cols:
        statements.append("ALTER TABLE media ADD COLUMN resource_type VARCHAR(50)")
    if "format" not in cols:
        statements.append("ALTER TABLE media ADD COLUMN format VARCHAR(50)")
    if "width" not in cols:
        statements.append("ALTER TABLE media ADD COLUMN width INTEGER")
    if "height" not in cols:
        statements.append("ALTER TABLE media ADD COLUMN height INTEGER")
    if "bytes" not in cols:
        statements.append("ALTER TABLE media ADD COLUMN bytes INTEGER")

    for stmt in statements:
        print("running:", stmt)
        conn.execute(text(stmt))

print("media columns update complete")
