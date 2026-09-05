import json
import os

import bcrypt
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from .db import get_db
from .models import AdminUser, Role, User
from .enums import ModuleAccess

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-this")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 din

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def authenticate_account(identifier: str, password: str, db: Session) -> tuple[str, str, str]:
    """Authenticate either an admin email or a registered user email/phone."""
    admin = db.query(AdminUser).filter(AdminUser.email == identifier).first()
    if admin and verify_password(password, admin.password_hash):
        return (
            create_access_token(data={"sub": admin.email, "role": admin.role, "slug": "admin"}),
            "admin",
            "/admin-dashboard",
        )

    user = db.query(User).filter(
        (User.email == identifier) | (User.phone == identifier)
    ).first()
    if user and verify_password(password, user.password_hash):
        return (
            create_access_token(data={"sub": str(user.id), "slug": "user"}),
            "user",
            "/user-dashboard",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )

def decode_access_token(token: str):
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def _normalize_modules(modules):
    """Normalize module permissions from DB JSON, dict, or string values."""
    if modules is None:
        return []
    if isinstance(modules, str):
        if not modules.strip():
            return []
        try:
            modules = json.loads(modules)
        except (TypeError, ValueError):
            return [modules]
    if isinstance(modules, dict):
        return [modules]
    if not isinstance(modules, list):
        return [modules]
    return modules


def _normalize_role_name(role_value: str | None) -> str:
    """Normalize legacy role names used in older database records."""
    raw = str(role_value or "admin").strip().lower().replace(" ", "_")
    aliases = {
        "super_admin": "superadmin",
        "super-admin": "superadmin",
        "superadmin": "superadmin",
        "admin": "admin",
    }
    return aliases.get(raw, raw)


def _role_modules_from_db(user_role: str, role_record: Role | None):
    """Return a module permission list for a user role, with safe fallback for admin/superadmin."""
    normalized_role = _normalize_role_name(user_role)
    if normalized_role in {"superadmin", "admin"}:
        return ["*"]
    if role_record is None:
        return []
    return _normalize_modules(role_record.modules)


def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    email = payload.get("sub")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing the subject.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(AdminUser).filter(AdminUser.email == email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    role_name = _normalize_role_name(user.role)
    is_superadmin = role_name == "superadmin"
    role = db.query(Role).filter(Role.name == role_name).first()
    if role is None and user.role is not None:
        legacy_alias = _normalize_role_name(user.role)
        if legacy_alias == "superadmin":
            role = db.query(Role).filter(Role.name == "superadmin").first()
    modules = _role_modules_from_db(role_name, role)

    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": role_name,
        "is_superadmin": is_superadmin,
        "modules": modules,
    }


def require_module(module_name: str):
    def dependency(current_admin: dict = Depends(get_current_admin)):
        modules = _normalize_modules(current_admin.get("modules", []))
        if "*" in modules:
            return current_admin

        for module in modules:
            if isinstance(module, dict):
                if module_name in module:
                    return current_admin
            elif module == module_name:
                return current_admin

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    return dependency


def require_module_access(module_name: str, access_level: ModuleAccess):
    """Check if user has the required access level for a module.

    Args:
        module_name: The name of the module
        access_level: The required access level (READ, UPDATE, or ALL)
    """
    def dependency(current_admin: dict = Depends(get_current_admin)):
        modules = _normalize_modules(current_admin.get("modules", []))

        # Admin users with "*" have full access
        if "*" in modules:
            return current_admin

        # Check for module access
        for module in modules:
            if isinstance(module, dict):
                # New format: {"module_name": "access_level"}
                if module_name in module:
                    user_access = str(module[module_name]).lower()
                    required_access = access_level.value.lower()

                    # ALL access covers everything, UPDATE covers READ and UPDATE, READ is READ only
                    if user_access == "all" or user_access == required_access or (user_access == "update" and required_access == "read"):
                        return current_admin
            elif module == module_name:
                # Backward compatible: simple module name in list = UPDATE access
                if access_level in [ModuleAccess.UPDATE, ModuleAccess.ALL]:
                    return current_admin
                elif access_level == ModuleAccess.READ:
                    # READ access granted if module is in list (backward compatible)
                    return current_admin

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. Required {access_level.value} access to {module_name}.",
        )

    return dependency


def require_super_admin(current_admin: dict = Depends(get_current_admin)):
    """Check if user is a super admin."""
    if not current_admin.get("is_superadmin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admin can access this resource.",
        )
    return current_admin