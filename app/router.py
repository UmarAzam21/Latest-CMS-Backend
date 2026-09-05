import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
import cloudinary
import cloudinary.uploader
from xml.etree import ElementTree as ET

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlalchemy.orm import Session
from .enums import ServiceType
from app.email_utils import send_email
from .db import get_db
from .models import AdminUser, ContentBlock, Lead, Page, Role, SiteSetting, Media, Message, PasswordResetToken, Notification
from .schema import (
    ContentBlockCreate,
    ContentBlockResponse,
    ForgotPasswordRequest,
    LeadsResponse,
    LoginRequest,
    MessageCreate,
    MessageResponse,
    ReplyRequest,
    ResetPasswordRequest,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    AssignRoleRequest,
    AdminUserWithRoleResponse,
    SiteSettingUpdate,
    SiteSettingResponse,
    SiteIdentity,
    SiteIdentityResponse,
    BrandAssets,
    BrandAssetsResponse,
    SocialMediaLinks,
    Token,
    AdminUserCreate,
    AdminUserResponse,
    AdminProfileUpdate,
    MeResponse,
    PageCreate,
    PageResponse,
    NotificationCreate,
    NotificationOut,
    NotificationListResponse,
    UnreadCountResponse,
    MarkReadRequest,
    LeadExportRequest,
)

import csv
from fastapi.responses import StreamingResponse



from sqlalchemy.orm import Session
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle


from .auth import authenticate_account, hash_password, verify_password, create_access_token, get_current_admin, require_module, require_super_admin
from .init_roles import init_builtin_roles, get_builtin_role_names

import os
from pathlib import Path




BASE_DIR = Path(__file__).resolve().parent
ENV_CANDIDATES = [
    BASE_DIR / ".env",
    BASE_DIR.parent / ".env",
]


def _find_env_file() -> Path | None:
    for candidate in ENV_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


FIELD_MAP = {
    "id": ("ID", lambda l: l.id),
    "username": ("Name", lambda l: l.username),
    "email": ("Email", lambda l: l.email),
    "phone": ("Phone", lambda l: l.phone),
    "service_type": ("Service Type", lambda l: l.service_type.value if hasattr(l.service_type, "value") else l.service_type),
    "city": ("City", lambda l: l.city),
    "created_at": ("Created At", lambda l: l.created_at.isoformat() if l.created_at else ""),
}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


ENV_FILE = _find_env_file()
if ENV_FILE is not None:
    _load_env_file(ENV_FILE)

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)


def upload_profile_image_to_cloudinary(file_obj: Any, filename: Optional[str] = None) -> str:
    """Upload a user profile image to Cloudinary and return its secure URL."""
    if file_obj is None:
        raise ValueError("No image file provided")

    file_data = file_obj.read() if hasattr(file_obj, "read") else file_obj
    if not file_data:
        raise ValueError("Uploaded image is empty")

    base_name = Path(filename or "profile-image").stem or "profile-image"
    public_id = f"admin_profile_{base_name}_{uuid.uuid4()}"

    upload_result = cloudinary.uploader.upload(
        file_data,
        resource_type="image",
        folder="admin/profile-images",
        public_id=public_id,
        transformation=[{"quality": "auto:good", "fetch_format": "auto"}],
    )
    return upload_result["secure_url"]


def build_admin_dashboard_summary(
    page_count: Optional[int] = None,
    new_message_count: Optional[int] = None,
    last_content_update: Optional[dict[str, Any]] = None,
    seo_health_score: Optional[int] = None,
    admin_leads: Optional[int] = None,
    recent_activity: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build the summary payload used by the admin dashboard cards and activity panel."""
    return {
        "total_pages": int(page_count or 0),
        "new_messages": int(new_message_count or 0),
        "last_content_update": last_content_update or {"label": "No content yet", "time": "N/A"},
        "seo_health_score": int(seo_health_score if seo_health_score is not None else 82),
        "admin_leads": int(admin_leads if admin_leads is not None else 0),
        "recent_activity": recent_activity or [],
    }


router = APIRouter()

# ---------------- Notifications (integrated) ----------------
from fastapi import WebSocket, WebSocketDisconnect, Query, BackgroundTasks
from sqlalchemy import func


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _read_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    try:
        shared_strings_xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ET.fromstring(shared_strings_xml)
    items = []
    for si in root.findall(f"{{{NS_MAIN}}}si"):
        texts = []
        for t in si.iter(f"{{{NS_MAIN}}}t"):
            texts.append(t.text or "")
        items.append("".join(texts))
    return items


def _get_cell_value(cell: ET.Element, shared_strings: List[str]) -> str:
    cell_type = cell.attrib.get("t")
    value_elem = cell.find(f"{{{NS_MAIN}}}v")
    if value_elem is None:
        value_elem = cell.find(f"{{{NS_MAIN}}}is")
    if value_elem is None:
        return ""

    raw_value = value_elem.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw_value)]
        except (ValueError, IndexError):
            return raw_value
    return raw_value


def parse_xlsx_upload(file_obj: Any, filename: str) -> dict[str, Any]:
    archive = zipfile.ZipFile(file_obj)
    try:
        workbook_xml = archive.read("xl/workbook.xml")
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid XLSX workbook") from exc

    workbook_root = ET.fromstring(workbook_xml)
    sheet_names: List[str] = []
    sheet_paths: List[str] = []

    for sheet in workbook_root.find(f"{{{NS_MAIN}}}sheets") or []:
        name = sheet.attrib.get("name")
        if name:
            sheet_names.append(name)
        relationship_id = sheet.attrib.get(f"{{{NS_REL}}}id")
        if relationship_id:
            sheet_paths.append(relationship_id)

    relationship_xml = archive.read("xl/_rels/workbook.xml.rels")
    rel_root = ET.fromstring(relationship_xml)
    rel_map = {
        rel.attrib.get("Id"): rel.attrib.get("Target")
        for rel in rel_root.findall(f"{{{NS_PACKAGE_REL}}}Relationship")
        if rel.attrib.get("Id") and rel.attrib.get("Target")
    }

    shared_strings = _read_shared_strings(archive)
    rows: List[List[str]] = []
    sheet_path = None

    if sheet_paths:
        target = rel_map.get(sheet_paths[0], "")
        if target.startswith("/"):
            sheet_path = target.lstrip("/")
        else:
            sheet_path = f"xl/{target}"

    if sheet_path is None:
        raise HTTPException(status_code=400, detail="No worksheet data was found in the uploaded file")

    worksheet_xml = archive.read(sheet_path)
    worksheet_root = ET.fromstring(worksheet_xml)
    sheet_data = worksheet_root.find(f"{{{NS_MAIN}}}sheetData")
    if sheet_data is None:
        return {
            "filename": filename,
            "sheet_names": sheet_names,
            "headers": [],
            "rows": [],
            "row_count": 0,
        }

    for row in sheet_data.findall(f"{{{NS_MAIN}}}row"):
        values = []
        for cell in row.findall(f"{{{NS_MAIN}}}c"):
            values.append(_get_cell_value(cell, shared_strings))
        rows.append(values)

    if not rows:
        return {
            "filename": filename,
            "sheet_names": sheet_names,
            "headers": [],
            "rows": [],
            "row_count": 0,
        }

    headers = [str(value).strip() or f"Column {index + 1}" for index, value in enumerate(rows[0])]
    data_rows = []
    for row in rows[1:]:
        if not row:
            continue
        record = {}
        for index, header in enumerate(headers):
            record[header] = row[index] if index < len(row) else ""
        data_rows.append(record)

    return {
        "filename": filename,
        "sheet_names": sheet_names,
        "headers": headers,
        "rows": data_rows,
        "row_count": len(data_rows),
    }


# Simple in-process WebSocket connection manager keyed by `user_id`.
class _WSManager:
    def __init__(self):
        # user_id -> list[WebSocket]
        self.connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(user_id, []).append(websocket)

    async def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        conns = self.connections.get(user_id)
        if not conns:
            return
        try:
            conns.remove(websocket)
        except ValueError:
            pass
        if not conns:
            self.connections.pop(user_id, None)

    async def push_to_user(self, user_id: str, payload: dict) -> None:
        conns = list(self.connections.get(user_id, []))
        dead = []
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for d in dead:
            try:
                self.connections.get(user_id, []).remove(d)
            except Exception:
                pass


ws_manager = _WSManager()


def resolve_notification_user_id(current_admin: Optional[dict] = None, db: Optional[Session] = None, fallback: str = "admin") -> str:
    """Return the canonical user id used for notifications and WebSocket routing.

    Prefer the authenticated admin email, then the admin numeric id, and finally a
    database-backed admin email as a fallback. This keeps the notification API and
    the websocket connection route in sync.
    """
    if isinstance(current_admin, dict):
        for key in ("email", "user_email"):
            value = current_admin.get(key)
            if value not in (None, ""):
                return str(value).strip()

        for key in ("id", "user_id"):
            value = current_admin.get(key)
            if value not in (None, ""):
                return str(value).strip()

        for key in ("name",):
            value = current_admin.get(key)
            if value not in (None, ""):
                return str(value).strip()

    if db is not None:
        admin = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
        if admin and admin.email:
            return str(admin.email).strip()

    return str(fallback).strip() or "admin"


async def notify(user_id: str, resource_type: str, title: str, resource_id: Optional[str] = None, type_: str = "system", message: Optional[str] = None, data: Optional[dict] = None, db: Session = None) -> None:
    """Create a notification and push it live to connected WebSocket clients.

    This helper can be imported and awaited from other parts of the application.
    If `db` is not provided, a new session is created for this operation.
    """
    close_db = False
    if db is None:
        db = next(get_db())
        close_db = True

    try:
        payload = NotificationCreate(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            type=type_,
            title=title,
            message=message,
            data=data,
        )
        # persist
        obj = Notification(
            user_id=payload.user_id,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            type=payload.type,
            title=payload.title,
            message=payload.message,
            data=payload.data,
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)

        # lightweight push payload
        push = {
            "id": obj.id,
            "title": obj.title,
            "message": obj.message,
            "resource_type": obj.resource_type,
            "resource_id": obj.resource_id,
            "type": obj.type.name if hasattr(obj.type, 'name') else str(obj.type),
            "created_at": obj.created_at.isoformat() if obj.created_at else None,
            "is_read": obj.is_read,
        }
        await ws_manager.push_to_user(user_id, push)
    finally:
        if close_db:
            try:
                db.close()
            except Exception:
                pass


async def notify_admin_event(current_admin: Optional[dict], db: Session, resource_type: str, resource_id: Optional[str], title: str, message: str, type_: str = "updated", data: Optional[dict] = None) -> None:
    await notify(
        user_id=resolve_notification_user_id(current_admin, db=db),
        resource_type=resource_type,
        resource_id=resource_id,
        type_=type_,
        title=title,
        message=message,
        data=data,
        db=db,
    )


@router.get("/api/notifications", response_model=NotificationListResponse)
def list_notifications(user_id: str = Query(...), unread_only: bool = Query(False), resource_type: Optional[str] = Query(None), page: int = Query(1), page_size: int = Query(25), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.is_read == False)
    if resource_type:
        q = q.filter(Notification.resource_type == resource_type)

    total = q.count()
    unread_count = db.query(func.count()).select_from(Notification).filter(Notification.user_id == user_id, Notification.is_read == False).scalar() or 0
    items = q.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return NotificationListResponse(items=items, total=total, unread_count=unread_count, page=page, page_size=page_size)


@router.get("/api/notifications/unread-count", response_model=UnreadCountResponse)
def notifications_unread_count(user_id: str = Query(...), db: Session = Depends(get_db)):
    count = db.query(func.count()).select_from(Notification).filter(Notification.user_id == user_id, Notification.is_read == False).scalar() or 0
    return UnreadCountResponse(unread_count=count)


@router.patch("/api/notifications/read")
def notifications_mark_read(request: MarkReadRequest, user_id: str = Query(...), db: Session = Depends(get_db)):
    now = datetime.utcnow()
    q = db.query(Notification).filter(Notification.user_id == user_id, Notification.is_read == False)
    if request.ids:
        q = q.filter(Notification.id.in_(request.ids))
    updated = q.update({"is_read": True, "read_at": now}, synchronize_session=False)
    db.commit()
    return {"updated": updated}


@router.delete("/api/notifications/{notification_id}")
def notifications_delete(notification_id: str, user_id: str = Query(...), db: Session = Depends(get_db)):
    obj = db.query(Notification).filter(Notification.user_id == user_id, Notification.id == notification_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(obj)
    db.commit()
    return {"deleted": True}


@router.websocket("/api/notifications/ws/{user_id}")
async def websocket_notifications(websocket: WebSocket, user_id: str):
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        await ws_manager.disconnect(user_id, websocket)

# ---------- Bootstrap Superadmin ----------
@router.get("/api/admin/diagnosis")
def diagnose_admin_users(db: Session = Depends(get_db)):
    """Diagnostic endpoint to check admin users in database. Useful for debugging."""
    all_users = db.query(AdminUser).all()
    superadmin = db.query(AdminUser).filter(AdminUser.role == "superadmin").first()
    
    return {
        "total_admin_users": len(all_users),
        "has_superadmin": superadmin is not None,
        "all_users": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "role": u.role,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in all_users
        ],
    }


@router.delete("/api/admin/reset-all-users")
def reset_all_admin_users(db: Session = Depends(get_db)):
    """DANGEROUS: Delete all admin users to reset the system. 
    Use this ONLY if you're locked out and need to start fresh."""
    count = db.query(AdminUser).delete()
    db.commit()
    return {
        "status": "success",
        "message": f"Deleted {count} admin user(s). You can now create a new superadmin.",
        "deleted_count": count,
    }


@router.post("/api/admin/create-user", response_model=AdminUserResponse)
def create_superadmin(user: AdminUserCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # Check if a superadmin already exists
    existing_superadmin = db.query(AdminUser).filter(AdminUser.role == "superadmin").first()
    if existing_superadmin:
        raise HTTPException(
            status_code=400,
            detail="Superadmin already exists. Use /api/admin/users to create additional admin users.",
        )

    new_user = AdminUser(
        name=user.name,
        email=user.email,
        password_hash=hash_password(user.password),
        role="superadmin"
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    background_tasks.add_task(
        notify_admin_event,
        {"email": new_user.email},
        db,
        "admin_user",
        str(new_user.id),
        "Super admin created",
        f"Super admin '{new_user.name}' was created.",
        "created",
        {"user_id": new_user.id, "email": new_user.email},
    )

    return new_user


@router.post("/api/admin/users", response_model=AdminUserResponse)
def create_admin_user(user: AdminUserCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Create a new admin user. Only super admin can create users."""
    existing = db.query(AdminUser).filter(AdminUser.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = AdminUser(
        name=user.name,
        email=user.email,
        password_hash=hash_password(user.password),
        role=user.role or "admin"
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "admin_user",
        str(new_user.id),
        f"Admin user '{new_user.name}' created",
        f"New admin '{new_user.email}' was created with role '{new_user.role}'.",
        "created",
        {"user_id": new_user.id, "email": new_user.email, "role": new_user.role},
    )

    return new_user

@router.get("/api/admin/users", response_model=list[AdminUserResponse])
def list_admin_users(db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """List all admin users. Only super admin can view users."""
    return db.query(AdminUser).all()


@router.put("/api/admin/users/{user_id}", response_model=AdminUserResponse)
def update_admin_user(user_id: int, user: AdminUserCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Update an admin user's name, email, and password. Only super admin can update users."""
    existing = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    existing.name = user.name
    existing.email = user.email
    if user.password:
        existing.password_hash = hash_password(user.password)

    db.commit()
    db.refresh(existing)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "admin_user",
        str(existing.id),
        f"Admin user '{existing.name}' updated",
        f"The admin account for '{existing.email}' was updated.",
        "updated",
        {"user_id": existing.id, "email": existing.email},
    )

    return existing

@router.post("/api/admin/users/{user_id}/assign-role", response_model=AdminUserWithRoleResponse)
def assign_role_to_user(user_id: int, request: AssignRoleRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Assign a role to a user. Only super admin can assign roles."""
    # Verify the role exists
    role = db.query(Role).filter(Role.name == request.role).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    
    # Verify the user exists
    user = db.query(AdminUser).filter(AdminUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent reassigning superadmin role
    if request.role == "superadmin" and user.role != "superadmin":
        raise HTTPException(status_code=400, detail="Cannot assign superadmin role")
    
    user.role = request.role
    db.commit()
    db.refresh(user)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "admin_user",
        str(user.id),
        f"Role assigned to '{user.name}'",
        f"User '{user.email}' was assigned the '{request.role}' role.",
        "updated",
        {"user_id": user.id, "role": request.role},
    )

    return user
@router.post("/api/admin/login", response_model=Token)
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    token, slug, redirect_to = authenticate_account(credentials.email, credentials.password, db)
    return {
        "access_token": token,
        "token_type": "bearer",
        "slug": slug,
        "redirect_to": redirect_to,
    }


        #   Protected Route
        
@router.get("/api/admin/me")
def read_current_admin(current_admin: dict = Depends(get_current_admin)):
    # Extract module names from modules list (which can contain dicts or strings)
    modules = current_admin.get("modules", [])
    permissions = []
    for m in modules:
        if isinstance(m, dict):
            permissions.extend(m.keys())
        else:
            permissions.append(m)

    db = next(get_db())
    try:
        user = db.query(AdminUser).filter(AdminUser.email == current_admin["email"]).first()
        profile_image = getattr(user, "profile_image", None) if user else None
        phone_number = getattr(user, "phone_number", None) if user else None
        bio = getattr(user, "bio", None) if user else None
    finally:
        db.close()

    return MeResponse(
        name=current_admin["name"],
        email=current_admin["email"],
        role=current_admin["role"],
        profile_image=profile_image,
        phone_number=phone_number,
        bio=bio,
        permissions=permissions,
    )


@router.get("/api/admin/dashboard")
def get_admin_dashboard(
    db: Session = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """Return the dashboard summary used by the admin home screen."""
    page_count = db.query(Page).count()
    new_message_count = db.query(Message).filter(Message.status == "new").count()

    latest_page = db.query(Page).order_by(Page.id.desc()).first()
    if latest_page:
        last_content_update = {"label": f"{latest_page.title} — {latest_page.slug}", "time": "Just now"}
    else:
        last_content_update = {"label": "No content yet", "time": "N/A"}

    recent_activity = []
    for page in db.query(Page).order_by(Page.id.desc()).limit(3):
        recent_activity.append({
            "title": f"Published page: {page.title}",
            "detail": f"{page.slug} • recently updated",
        })

    if not recent_activity:
        recent_activity = [{
            "title": "Create a new page",
            "detail": "Start building your site",
        }]

    admin_leads = db.query(Lead).count()
    seo_health_score = 82 if page_count == 0 else min(99, 78 + page_count)

    return build_admin_dashboard_summary(
        page_count=page_count,
        new_message_count=new_message_count,
        last_content_update=last_content_update,
        seo_health_score=seo_health_score,
        admin_leads=admin_leads,
        recent_activity=recent_activity,
    )


@router.get("/api/admin/overview")
def get_admin_overview(
    db: Session = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    return get_admin_dashboard(db=db, current_admin=current_admin)


@router.post("/api/admin/profile/upload-image")
async def upload_profile_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """Upload the current admin's profile photo to Cloudinary and persist the secure URL."""
    user = db.query(AdminUser).filter(AdminUser.email == current_admin["email"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="Admin user not found")

    try:
        profile_image_url = upload_profile_image_to_cloudinary(file.file, filename=file.filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not upload profile image: {exc}") from exc

    user.profile_image = profile_image_url
    db.commit()
    db.refresh(user)

    await notify_admin_event(
        current_admin,
        db,
        "admin_user",
        str(user.id),
        "Profile image updated",
        f"Profile image for '{user.name}' was updated.",
        "updated",
        {"user_id": user.id, "profile_image": profile_image_url},
    )

    return {"profile_image": user.profile_image}


@router.patch("/api/admin/profile", response_model=AdminUserResponse)
def update_own_profile(
    payload: AdminProfileUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    """Allow the logged-in admin to update their basic profile details."""
    user = db.query(AdminUser).filter(AdminUser.email == current_admin["email"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="Admin user not found")

    if payload.name is not None:
        user.name = payload.name.strip() or user.name

    if payload.profile_image is not None and hasattr(user, "profile_image"):
        cleaned_url = payload.profile_image.strip()
        user.profile_image = cleaned_url or None

    if payload.phone_number is not None and hasattr(user, "phone_number"):
        user.phone_number = payload.phone_number.strip() or None

    if payload.bio is not None and hasattr(user, "bio"):
        user.bio = payload.bio.strip() or None

    if payload.current_password is not None or payload.new_password is not None:
        if not payload.current_password or not payload.new_password:
            raise HTTPException(status_code=400, detail="Both current_password and new_password are required together.")
        if not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
        if len(payload.new_password.strip()) < 6:
            raise HTTPException(status_code=400, detail="New password must be at least 6 characters long.")
        user.password_hash = hash_password(payload.new_password.strip())

    db.commit()
    db.refresh(user)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "admin_user",
        str(user.id),
        "Profile updated",
        f"The profile for '{user.name}' was updated.",
        "updated",
        {"user_id": user.id, "email": user.email},
    )

    return user


@router.post("/api/admin/roles", response_model=RoleResponse)
def create_role(role: RoleCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Create a new role with module access levels. Only super admin can create roles."""
    existing = db.query(Role).filter(Role.name == role.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Role already exists")

    new_role = Role(name=role.name, label=role.label, modules=role.modules)
    db.add(new_role)
    db.commit()
    db.refresh(new_role)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "role",
        new_role.name,
        f"Role '{new_role.name}' created",
        f"The role '{new_role.label}' was created.",
        "created",
        {"role": new_role.name, "label": new_role.label},
    )

    return new_role


@router.get("/api/admin/roles", response_model=list[RoleResponse])
def list_roles(db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """List all roles. Only super admin can view roles."""
    return db.query(Role).all()


@router.get("/api/admin/roles/{role_name}", response_model=RoleResponse)
def get_role(role_name: str, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Get a specific role. Only super admin can view roles."""
    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.put("/api/admin/roles/{role_name}", response_model=RoleResponse)
def update_role(role_name: str, payload: RoleUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Update a role's modules and access levels. Only super admin can update roles."""
    existing = db.query(Role).filter(Role.name == role_name).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Role not found")

    if payload.label is not None:
        existing.label = payload.label
    if payload.modules is not None:
        existing.modules = payload.modules

    db.commit()
    db.refresh(existing)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "role",
        existing.name,
        f"Role '{existing.name}' updated",
        f"The role '{existing.label}' was updated.",
        "updated",
        {"role": existing.name, "label": existing.label},
    )

    return existing


@router.delete("/api/admin/roles/{role_name}")
def delete_role(role_name: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Delete a role. Only super admin can delete roles."""
    if role_name == "superadmin":
        raise HTTPException(status_code=400, detail="Cannot delete superadmin role")
    
    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    role_name_text = role.name
    db.delete(role)
    db.commit()

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "role",
        role_name_text,
        f"Role '{role_name_text}' deleted",
        f"The role '{role_name_text}' was deleted.",
        "deleted",
        {"role": role_name_text},
    )

    return {"deleted": True}


@router.post("/api/admin/roles/init-builtin")
def initialize_builtin_roles(background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_super_admin)):
    """Initialize/reset built-in roles. Only super admin can do this."""
    try:
        init_builtin_roles(db)
        background_tasks.add_task(
            notify_admin_event,
            current_admin,
            db,
            "role",
            "builtin_roles",
            "Built-in roles initialized",
            "Built-in roles were initialized or refreshed.",
            "updated",
            {"scope": "builtin_roles"},
        )
        return {"status": "success", "message": "Built-in roles initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/roles/builtin/list")
def list_builtin_roles(current_admin: dict = Depends(require_super_admin)):
    """List all built-in role names. Only super admin can view."""
    builtin_roles = get_builtin_role_names()
    return {"builtin_roles": builtin_roles, "count": len(builtin_roles)}


@router.post("/api/admin/pages", response_model=PageResponse)
def create_page(page: PageCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_module("pages"))):
    existing = db.query(Page).filter(Page.slug == page.slug).first()
    if existing:
        raise HTTPException(status_code=400, detail="Page with this slug already exists")

    new_page = Page(slug=page.slug, title=page.title, meta_data=page.meta_data)
    db.add(new_page)
    db.commit()
    db.refresh(new_page)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "page",
        str(new_page.id),
        f"Page '{new_page.slug}' created",
        f"A new page named '{new_page.title}' was created.",
        "created",
        {"slug": new_page.slug, "title": new_page.title},
    )

    return new_page


@router.get("/api/admin/pages", response_model=list[PageResponse])
def list_pages(db: Session = Depends(get_db), current_admin: dict = Depends(require_module("pages"))):
    return db.query(Page).all()


# ================= CONTENT BLOCKS (Admin - Protected) =================

@router.post("/api/admin/content", response_model=ContentBlockResponse)
def create_content_block(block: ContentBlockCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    new_block = ContentBlock(
        page_id=block.page_id,
        block_key=block.block_key,
        block_type=block.block_type,
        content=block.content,
        order_index=block.order_index
    )
    db.add(new_block)
    db.commit()
    db.refresh(new_block)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "content_block",
        str(new_block.id),
        f"Content block '{new_block.block_key}' created",
        f"Block '{new_block.block_key}' was created for page #{new_block.page_id}.",
        "created",
        {"page_id": new_block.page_id, "block_key": new_block.block_key},
    )

    return new_block


@router.get("/api/admin/content/{page_id}", response_model=list[ContentBlockResponse])
def get_page_content_admin(page_id: int, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    return db.query(ContentBlock).filter(ContentBlock.page_id == page_id).order_by(ContentBlock.order_index).all()


@router.put("/api/admin/content/{block_id}", response_model=ContentBlockResponse)
def update_content_block(block_id: int, block: ContentBlockCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    existing_block = db.query(ContentBlock).filter(ContentBlock.id == block_id).first()
    if not existing_block:
        raise HTTPException(status_code=404, detail="Content block not found")

    existing_block.content = block.content
    existing_block.block_type = block.block_type
    existing_block.order_index = block.order_index
    db.commit()
    db.refresh(existing_block)

    background_tasks.add_task(
        notify,
        current_admin.get("email", "admin"),
        "content_block",
        "Content block updated",
        str(existing_block.id),
        "updated",
        f"Block '{existing_block.block_key}' was updated",
        {"page_id": existing_block.page_id, "block_key": existing_block.block_key},
    )

    return existing_block


@router.delete("/api/admin/content/{block_id}")
def delete_content_block(block_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    existing_block = db.query(ContentBlock).filter(ContentBlock.id == block_id).first()
    if not existing_block:
        raise HTTPException(status_code=404, detail="Content block not found")

    block_key = existing_block.block_key
    page_id = existing_block.page_id
    db.delete(existing_block)
    db.commit()

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "content_block",
        str(block_id),
        f"Content block '{block_key}' deleted",
        f"Block '{block_key}' was deleted from page #{page_id}.",
        "deleted",
        {"page_id": page_id, "block_key": block_key},
    )

    return {"status": "deleted"}


# ================= PUBLIC ENDPOINTS (No Auth - Frontend Uses These) =================

@router.get("/api/public/pages/{slug}")
def get_public_page(slug: str, db: Session = Depends(get_db)):
    page = db.query(Page).filter(Page.slug == slug).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    blocks = db.query(ContentBlock).filter(ContentBlock.page_id == page.id).order_by(ContentBlock.order_index).all()

    result = {}
    for block in blocks:
        result[block.block_key] = {
            "type": block.block_type,
            "value": block.content.get("value") if isinstance(block.content, dict) else block.content
        }

    return {"page": page.slug, "title": page.title, "blocks": result}


# ================= SITE SETTINGS (Admin - Protected) =================

@router.put("/api/admin/settings/{key}", response_model=SiteSettingResponse)
def update_setting(key: str, setting: SiteSettingUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(require_module("settings"))):
    existing = db.query(SiteSetting).filter(SiteSetting.key == key).first()

    if existing:
        existing.value = setting.value
        db.commit()
        db.refresh(existing)

        background_tasks.add_task(
            notify,
            current_admin.get("email", "admin"),
            "site_setting",
            f"Site setting '{key}' updated",
            key,
            "updated",
            f"The '{key}' setting was updated",
            {"key": key},
        )

        return existing
    else:
        new_setting = SiteSetting(key=key, value=setting.value)
        db.add(new_setting)
        db.commit()
        db.refresh(new_setting)

        background_tasks.add_task(
            notify,
            current_admin.get("email", "admin"),
            "site_setting",
            f"Site setting '{key}' created",
            key,
            "created",
            f"The '{key}' setting was created",
            {"key": key},
        )

        return new_setting


@router.get("/api/admin/settings", response_model=list[SiteSettingResponse])
def list_settings(db: Session = Depends(get_db), current_admin: dict = Depends(require_module("settings"))):
    return db.query(SiteSetting).all()


# ================= PUBLIC SETTINGS (No Auth) =================

@router.get("/api/public/settings")
def get_public_settings(db: Session = Depends(get_db)):
    settings = db.query(SiteSetting).all()
    result = {}
    for s in settings:
        result[s.key] = s.value
    return result


# ================= SITE IDENTITY =================

@router.get("/api/admin/site-identity", response_model=SiteIdentityResponse)
def get_site_identity(db: Session = Depends(get_db), current_admin: dict = Depends(require_module("settings"))):
    """Get site identity settings"""
    setting = db.query(SiteSetting).filter(SiteSetting.key == "site_identity").first()
    if not setting:
        # Return default values if not set
        return SiteIdentityResponse(
            site_name="",
            tagline="",
            contact_form_notification_email="admin@example.com",
            admin_email="admin@example.com",
            timezone="UTC",
            language="en-US"
        )
    return SiteIdentityResponse(**setting.value)


@router.put("/api/admin/site-identity", response_model=SiteIdentityResponse)
def update_site_identity(
    site_identity: SiteIdentity,
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module("settings"))
):
    """Update site identity settings"""
    existing = db.query(SiteSetting).filter(SiteSetting.key == "site_identity").first()
    
    if existing:
        existing.value = site_identity.model_dump()
        db.commit()
        db.refresh(existing)
    else:
        new_setting = SiteSetting(key="site_identity", value=site_identity.model_dump())
        db.add(new_setting)
        db.commit()
        db.refresh(new_setting)
    
    return SiteIdentityResponse(**existing.value if existing else new_setting.value)


# ================= BRAND ASSETS =================

@router.get("/api/admin/brand-assets", response_model=BrandAssetsResponse)
def get_brand_assets(db: Session = Depends(get_db), current_admin: dict = Depends(require_module("settings"))):
    """Get brand assets (logo, favicon, social media links)"""
    setting = db.query(SiteSetting).filter(SiteSetting.key == "brand_assets").first()
    if not setting:
        return BrandAssetsResponse(
            logo_url=None,
            favicon_url=None,
            social_media=SocialMediaLinks()
        )
    return BrandAssetsResponse(**setting.value)


@router.put("/api/admin/brand-assets", response_model=BrandAssetsResponse)
def update_brand_assets(
    brand_assets: BrandAssets,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module("settings"))
):
    """Update brand assets"""
    existing = db.query(SiteSetting).filter(SiteSetting.key == "brand_assets").first()
    
    if existing:
        existing.value = brand_assets.model_dump(exclude_none=False)
        db.commit()
        db.refresh(existing)
    else:
        new_setting = SiteSetting(key="brand_assets", value=brand_assets.model_dump(exclude_none=False))
        db.add(new_setting)
        db.commit()
        db.refresh(new_setting)

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "brand_assets",
        "brand_assets",
        "Brand assets updated",
        "The brand assets configuration was updated.",
        "updated",
        {"key": "brand_assets"},
    )
    
    return BrandAssetsResponse(**existing.value if existing else new_setting.value)


@router.post("/api/admin/brand-assets/upload-logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module("settings"))
):
    """Upload logo file to Cloudinary"""
    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            resource_type="auto",
            folder="site-branding/logos",
            public_id=f"logo_{uuid.uuid4()}"
        )
        
        # Save to database
        existing = db.query(SiteSetting).filter(SiteSetting.key == "brand_assets").first()
        if existing:
            existing.value["logo_url"] = upload_result["secure_url"]
            existing.value["logo_public_id"] = upload_result["public_id"]
        else:
            existing = SiteSetting(
                key="brand_assets",
                value={
                    "logo_url": upload_result["secure_url"],
                    "logo_public_id": upload_result["public_id"]
                }
            )
            db.add(existing)
        
        db.commit()
        await notify_admin_event(
            current_admin,
            db,
            "brand_assets",
            "brand_assets",
            "Logo uploaded",
            "A new site logo was uploaded.",
            "created",
            {"logo_url": upload_result["secure_url"]},
        )
        return {"success": True, "url": upload_result["secure_url"], "public_id": upload_result["public_id"]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/admin/brand-assets/upload-favicon")
async def upload_favicon(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module("settings"))
):
    """Upload favicon file to Cloudinary"""
    try:
        upload_result = cloudinary.uploader.upload(
            file.file,
            resource_type="auto",
            folder="site-branding/favicons",
            public_id=f"favicon_{uuid.uuid4()}"
        )
        
        # Save to database
        existing = db.query(SiteSetting).filter(SiteSetting.key == "brand_assets").first()
        if existing:
            existing.value["favicon_url"] = upload_result["secure_url"]
            existing.value["favicon_public_id"] = upload_result["public_id"]
        else:
            existing = SiteSetting(
                key="brand_assets",
                value={
                    "favicon_url": upload_result["secure_url"],
                    "favicon_public_id": upload_result["public_id"]
                }
            )
            db.add(existing)
        
        db.commit()
        await notify_admin_event(
            current_admin,
            db,
            "brand_assets",
            "brand_assets",
            "Favicon uploaded",
            "A new site favicon was uploaded.",
            "created",
            {"favicon_url": upload_result["secure_url"]},
        )
        return {"success": True, "url": upload_result["secure_url"], "public_id": upload_result["public_id"]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ================= ADMIN — Inbox Dekhna =================

@router.post("/api/messages", response_model=MessageResponse)
async def create_message(payload: MessageCreate, db: Session = Depends(get_db)):
    msg = Message(
        name=payload.name,
        email=payload.email,
        services=payload.services,
        message=payload.message,
        status="new",
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    await notify_admin_event(
        None,
        db,
        "message",
        str(msg.id),
        "New message received",
        f"New message from {msg.name}.",
        "created",
        {"message_id": msg.id, "email": msg.email},
    )
    return msg


@router.get("/api/admin/messages", response_model=list[MessageResponse])
def list_messages(db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    return db.query(Message).order_by(Message.created_at.desc()).all()


@router.get("/api/admin/messages/counts")
def message_counts(db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    """Return the live inbox totals used by the admin message badges."""
    return {
        "total": db.query(Message).count(),
        "unread": db.query(Message).filter(Message.status == "new").count(),
        "replied": db.query(Message).filter(Message.status == "replied").count(),
    }


@router.get("/api/admin/messages/{message_id}", response_model=MessageResponse)
async def get_message(message_id: int, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    notification_user_id = resolve_notification_user_id(current_admin, db=db)
    message_was_unread = msg.status == "new"
    matching_notifications = db.query(Notification).filter(
        Notification.user_id == notification_user_id,
        Notification.resource_type == "message",
        Notification.resource_id == str(msg.id),
        Notification.is_read == False,
    )
    notification_was_unread = matching_notifications.first() is not None

    if message_was_unread:
        msg.status = "read"

    if notification_was_unread:
        matching_notifications.update(
            {"is_read": True, "read_at": datetime.now(timezone.utc)},
            synchronize_session=False,
        )

    if message_was_unread or notification_was_unread:
        db.commit()

    await ws_manager.push_to_user(
        notification_user_id,
        {
            "event": "message_read",
            "message_id": msg.id,
            "unread": db.query(Message).filter(Message.status == "new").count(),
            "notification_unread_count": db.query(Notification).filter(
                Notification.user_id == notification_user_id,
                Notification.is_read == False,
            ).count(),
        },
    )

    return msg


@router.patch("/api/admin/messages/{message_id}/read")
async def mark_message_read(message_id: int, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    notification_user_id = resolve_notification_user_id(current_admin, db=db)
    if msg.status == "new":
        msg.status = "read"

    db.query(Notification).filter(
        Notification.user_id == notification_user_id,
        Notification.resource_type == "message",
        Notification.resource_id == str(msg.id),
        Notification.is_read == False,
    ).update(
        {"is_read": True, "read_at": datetime.now(timezone.utc)},
        synchronize_session=False,
    )
    db.commit()

    unread = db.query(Message).filter(Message.status == "new").count()
    notification_unread_count = db.query(Notification).filter(
        Notification.user_id == notification_user_id,
        Notification.is_read == False,
    ).count()
    await ws_manager.push_to_user(
        notification_user_id,
        {
            "event": "message_read",
            "message_id": msg.id,
            "unread": unread,
            "notification_unread_count": notification_unread_count,
        },
    )
    return {
        "message_id": msg.id,
        "unread": unread,
        "notification_unread_count": notification_unread_count,
    }


@router.delete("/api/admin/messages/{message_id}")
def delete_message(message_id: int, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    db.delete(msg)
    db.commit()
    return {"status": "deleted"}


@router.put("/api/admin/pages/{page_id}/meta")
def update_page_meta(page_id: int, meta_data: dict, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    page = db.query(Page).filter(Page.id == page_id).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    page.meta_data = meta_data
    db.commit()
    db.refresh(page)

    background_tasks.add_task(
        notify,
        current_admin.get("email", "admin"),
        "page",
        f"Page '{page.slug}' metadata updated",
        str(page.id),
        "updated",
        f"The metadata for page '{page.slug}' was updated",
        {"slug": page.slug},
    )

    return page


@router.post("/api/admin/messages/{message_id}/reply")
def reply_to_message(message_id: int, reply: ReplyRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    email_sent = False
    try:
        email_sent = send_email(
            to_email=msg.email,
            subject=f"Re: {msg.services or 'Your inquiry'}",
            body=reply.reply_message,
        )
    except Exception:
        email_sent = False

    msg.status = "replied"
    db.commit()

    background_tasks.add_task(
        notify_admin_event,
        current_admin,
        db,
        "message",
        str(msg.id),
        "Message replied",
        f"Reply sent for '{msg.name}' regarding '{msg.services or 'your inquiry'}'.",
        "updated",
        {"message_id": msg.id, "email": msg.email},
    )

    if not email_sent:
        return {
            "status": "Reply saved successfully",
            "warning": "The email could not be delivered right now. Please check your SMTP settings.",
        }

    return {"status": "Reply sent successfully"}



@router.get("/api/public/services")
def get_available_services():
    return [{"value": s.name, "label": s.value} for s in ServiceType]



@router.patch("/api/admin/content/{block_id}/toggle-publish")
def toggle_publish(block_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    block = db.query(ContentBlock).filter(ContentBlock.id == block_id).first()
    if not block:
        raise HTTPException(status_code=404, detail="Content block not found")

    block.is_published = not block.is_published
    db.commit()
    db.refresh(block)

    background_tasks.add_task(
        notify,
        current_admin.get("email", "admin"),
        "content_block",
        f"Content block {'published' if block.is_published else 'unpublished'}",
        str(block.id),
        "updated",
        f"The block '{block.block_key}' was {'published' if block.is_published else 'unpublished'}",
        {"block_key": block.block_key, "page_id": block.page_id},
    )

    return block




@router.post("/api/admin/forgot-password")
def forgot_password(request: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(AdminUser).filter(AdminUser.email == request.email).first()
    if not user:
        # Security ke liye hamesha same message dein, chahe email exist kare ya na kare
        return {"status": "If this email exists, a reset link has been sent."}

    token = str(uuid.uuid4())
    expires = datetime.utcnow() + timedelta(minutes=30)

    reset_entry = PasswordResetToken(email=user.email, token=token, expires_at=expires)
    db.add(reset_entry)
    db.commit()

    reset_link = f"http://localhost:3000/reset-password?token={token}"   # frontend URL (jo bhi ho)

    try:
        send_email(
            to_email=user.email,
            subject="Password Reset Request",
            body=f"Click the link to reset your password (valid for 30 minutes):\n\n{reset_link}"
        )
    except Exception as e:
        print(f"Failed to send reset email: {e}")

    return {"status": "If this email exists, a reset link has been sent."}


# ---------- Step B: Naya Password Set Karna ----------
@router.post("/api/admin/reset-password")
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    token_entry = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == request.token,
        PasswordResetToken.used == False
    ).first()

    if not token_entry:
        raise HTTPException(status_code=400, detail="Invalid or already used token")

    expires_at = token_entry.expires_at
    if expires_at is None:
        raise HTTPException(status_code=400, detail="Token has no expiry date")

    if expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)

    if expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Token has expired")

    user = db.query(AdminUser).filter(AdminUser.email == token_entry.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(request.new_password)
    token_entry.used = True
    db.commit()

    return {"status": "Password reset successfully"}














@router.get("/api/admin/media")
def list_media(
    resource_type: Optional[str] = Query(None, description="Filter: 'image' or 'video'"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
):
    q = db.query(Media)
    if resource_type:
        q = q.filter(Media.resource_type == resource_type)

    total = q.count()
    items = (
        q.order_by(Media.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@router.post("/api/admin/import/xlsx")
async def import_xlsx(
    file: UploadFile = File(...),
    current_admin: dict = Depends(get_current_admin),
):
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are supported")

    contents = await file.read()
    parsed = parse_xlsx_upload(io.BytesIO(contents), file.filename)

    try:
        db = next(get_db())
        db.close()
    except Exception:
        pass

    return {
        "message": "XLSX staged for import",
        "filename": parsed["filename"],
        "sheet_names": parsed["sheet_names"],
        "headers": parsed["headers"],
        "row_count": parsed["row_count"],
        "rows": parsed["rows"],
        "status": "staged",
    }


@router.post("/api/admin/media/upload")
async def upload_media(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: dict = Depends(get_current_admin),
    filename: Optional[str] = Query(None, description="Optional custom filename for the uploaded media")
):
    if not os.getenv("CLOUDINARY_CLOUD_NAME") or not os.getenv("CLOUDINARY_API_KEY") or not os.getenv("CLOUDINARY_API_SECRET"):
        raise HTTPException(
            status_code=500,
            detail="Cloudinary credentials are not configured. Check app/.env for CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET.",
        )

    try:
        # resource_type="auto" => Cloudinary khud detect karega image/video/raw
        result = cloudinary.uploader.upload(
            file.file,
            resource_type="auto",
            folder="filernow",  # organize karne ke liye
            filename=filename if filename else file.filename,
            overwrite=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    media = Media(
        url=result["secure_url"],
        public_id=result["public_id"],
        resource_type=result["resource_type"],   # "image" / "video"
        format=result.get("format"),
        width=result.get("width"),
        height=result.get("height"),
        bytes=result.get("bytes"),
    )
    db.add(media)
    db.commit()
    db.refresh(media)

    await notify(
        user_id=resolve_notification_user_id(current_admin, db=db),
        resource_type="media",
        resource_id=str(media.id),
        type_="created",
        title="New media uploaded",
        message=f"{media.resource_type} uploaded: {media.public_id}",
        db=db,
    )

    return media




@router.delete("/api/admin/media/{media_id}")
async def delete_media(media_id: int, db: Session = Depends(get_db), current_admin: dict = Depends(get_current_admin)):
    media = db.query(Media).filter(Media.id == media_id).first()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    cloudinary.uploader.destroy(media.public_id, resource_type=media.resource_type)

    db.delete(media)
    db.commit()
    return {"status": "deleted"}



# @router.post("/api/admin/leads", response_model=LeadsResponse)
# async def create_lead(
#     message: LeadsResponse,
#     db: Session = Depends(get_db)
# ):
#     new_lead = Lead(
#         username=message.username,
#         email=message.email,
#         phone=message.phone,
#         service_type=message.service_type,
#         city=message.city,
#     )

#     db.add(new_lead)
#     db.commit()
#     db.refresh(new_lead)

#     admin_user = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
#     await notify(
#         user_id=resolve_notification_user_id({"email": admin_user.email} if admin_user and admin_user.email else None, db=db),
#         resource_type="lead",
#         resource_id=str(new_lead.id),
#         type_="created",
#         title="New lead received",
#         message=f"Lead from {new_lead.username} ({new_lead.email})",
#         db=db,
#     )

#     return new_lead



# @router.get("/api/admin/leads", response_model=list[LeadsResponse])
# async def get_leads(
#     db: Session = Depends(get_db),
#     skip: int = 0,
#     limit: int = 50,
# ):
#     leads = (
#         db.query(Lead)
#         .order_by(Lead.created_at.desc())
#         .offset(skip)
#         .limit(limit)
#         .all()
#     )
#     return leads

# @router.patch("/api/admin/leads/{lead_id}", response_model=LeadsResponse)
# async def update_lead(
#     lead_id: int,
#     lead_update: LeadsResponse,
#     db: Session = Depends(get_db)
# ):
#     lead = db.query(Lead).filter(Lead.id == lead_id).first()
#     if not lead:
#         raise HTTPException(status_code=404, detail="Lead not found")

#     lead.username = lead_update.username
#     lead.email = lead_update.email
#     lead.phone = lead_update.phone
#     lead.service_type = lead_update.service_type
#     lead.city = lead_update.city

#     db.commit()
#     db.refresh(lead)

#     admin_user = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
#     await notify(
#         user_id=resolve_notification_user_id({"email": admin_user.email} if admin_user and admin_user.email else None, db=db),
#         resource_type="lead",
#         resource_id=str(lead.id),
#         type_="updated",
#         title="Lead updated",
#         message=f"Lead {lead.username} ({lead.email}) was updated",
#         db=db,
#     )

#     return lead



def get_leads_for_export(db: Session, payload: LeadExportRequest) -> list[Lead]:
    query = db.query(Lead)

    if payload.select_all:
        if payload.service_type:
            query = query.filter(Lead.service_type == payload.service_type)
    elif payload.lead_ids:
        query = query.filter(Lead.id.in_(payload.lead_ids))
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either lead_ids or set select_all=true",
        )

    leads = query.order_by(Lead.created_at.desc()).all()
    if not leads:
        raise HTTPException(status_code=404, detail="No leads found for export")

    return leads


def resolve_fields(fields: list[str] | None) -> list[str]:
    selected = fields or list(FIELD_MAP.keys())
    invalid = [f for f in selected if f not in FIELD_MAP]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid fields: {invalid}")
    return selected


def build_csv(leads: list[Lead], fields: list[str]) -> io.StringIO:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([FIELD_MAP[f][0] for f in fields])
    for lead in leads:
        writer.writerow([FIELD_MAP[f][1](lead) for f in fields])
    buffer.seek(0)
    return buffer


def build_excel(leads: list[Lead], fields: list[str]) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append([FIELD_MAP[f][0] for f in fields])
    for lead in leads:
        ws.append([FIELD_MAP[f][1](lead) for f in fields])

    for col_cells in ws.columns:
        max_len = max(len(str(c.value)) for c in col_cells if c.value is not None)
        ws.column_dimensions[col_cells[0].column_letter].width = max_len + 4

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def build_pdf(leads: list[Lead], fields: list[str]) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))

    header = [FIELD_MAP[f][0] for f in fields]
    rows = [[str(FIELD_MAP[f][1](lead)) for f in fields] for lead in leads]
    data = [header] + rows

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d2d2d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    doc.build([table])
    buffer.seek(0)
    return buffer



# @router.post("/api/admin/leads/download")
# async def download_leads(
#     payload: LeadExportRequest,
#     db: Session = Depends(get_db),
#     # admin: AdminUser = Depends(get_current_admin_user),
# ):
#     leads = get_leads_for_export(db, payload)
#     fields = resolve_fields(payload.fields)
#     timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

#     if payload.format == "csv":
#         buffer = build_csv(leads, fields)
#         return StreamingResponse(
#             iter([buffer.getvalue()]),
#             media_type="text/csv",
#             headers={"Content-Disposition": f"attachment; filename=leads_{timestamp}.csv"},
#         )

#     if payload.format == "excel":
#         buffer = build_excel(leads, fields)
#         return StreamingResponse(
#             buffer,
#             media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#             headers={"Content-Disposition": f"attachment; filename=leads_{timestamp}.xlsx"},
#         )

#     if payload.format == "pdf":
#         buffer = build_pdf(leads, fields)
#         return StreamingResponse(
#             buffer,
#             media_type="application/pdf",
#             headers={"Content-Disposition": f"attachment; filename=leads_{timestamp}.pdf"},
#         )