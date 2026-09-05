from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy import inspect

from .db import engine, Base, get_db
from . import models
from .router import router
from .support_system.router import router as support_router
from .auth import hash_password

from app.xlsx_import.router import router as xlsx_import_router
from app.xlsx_import.control import init_control_tables
from app.init_roles import init_builtin_roles
from .leads.router import router as lead_router
from .user_account.router import router as user_account_router


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


def ensure_admin_profile_columns():
    try:
        inspector = inspect(engine)
        columns = [col["name"] for col in inspector.get_columns("admin_users")]
        missing_columns = []

        for column_name, column_type in {
            "profile_image": "VARCHAR(1000)",
            "phone_number": "VARCHAR(50)",
            "bio": "TEXT",
        }.items():
            if column_name not in columns:
                missing_columns.append((column_name, column_type))

        if missing_columns:
            with engine.begin() as conn:
                for column_name, column_type in missing_columns:
                    conn.execute(f"ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS {column_name} {column_type}")
            logging.getLogger(__name__).info("Added missing admin_users columns: %s", [name for name, _ in missing_columns])
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not ensure admin_users profile columns: %s", exc)


def ensure_default_superadmin():
    """Create a usable default superadmin if the database is empty."""
    db = next(get_db())
    try:
        from .models import AdminUser

        has_superadmin = db.query(AdminUser).filter(AdminUser.role == "superadmin").first() is not None
        if not has_superadmin:
            email = os.getenv("DEFAULT_SUPERADMIN_EMAIL", "admin@example.com")
            password = os.getenv("DEFAULT_SUPERADMIN_PASSWORD", "admin123")
            name = os.getenv("DEFAULT_SUPERADMIN_NAME", "System Admin")

            admin = AdminUser(
                name=name,
                email=email,
                password_hash=hash_password(password),
                role="superadmin",
            )
            db.add(admin)
            db.commit()
            logging.getLogger(__name__).info("Created default superadmin with email %s", email)
    finally:
        db.close()


# ---------------------------------------------------------
# Lifespan
# ---------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup and shutdown events.
    """

    # Create database tables
    Base.metadata.create_all(bind=engine)

    # Backfill legacy database schemas that predate the profile-related columns.
    ensure_admin_profile_columns()

    # Initialize built-in roles
    db = next(get_db())
    try:
        init_builtin_roles(db)
    finally:
        db.close()

    # Ensure there is a usable default superadmin for fresh installs.
    ensure_default_superadmin()

    # Initialize XLSX import control tables
    init_control_tables()

    yield

    # Shutdown logic can be added here if required


# ---------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------

app = FastAPI(
    title="CMS Backend API",
    lifespan=lifespan
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})
    openapi_schema["components"]["securitySchemes"]["HTTPBearer"] = {
        "type": "http",
        "scheme": "bearer",
    }
    openapi_schema["security"] = [{"HTTPBearer": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

allow_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000"
).split(",")

allow_origins = [origin.strip() for origin in allow_origins]


app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Routers
# ---------------------------------------------------------

app.include_router(router)

app.include_router(
    support_router,
    prefix="/api"
)

app.include_router(
    xlsx_import_router,
    prefix="/api"
)

app.include_router(lead_router)
app.include_router(user_account_router)


# ---------------------------------------------------------
# Root Endpoint
# ---------------------------------------------------------

@app.get("/")
def read_root():
    return {
        "status": "Backend is running"
    }