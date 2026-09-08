from pydantic import BaseModel, ConfigDict, EmailStr, Field,field_validator
from typing import Optional, Any, Union
from datetime import datetime
import uuid
from .enums import ServiceType
from typing import Literal


# ---------- Admin User ----------
class AdminUserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str = "admin"

class AdminUserResponse(BaseModel):
    id: int
    name: Optional[str] = None
    email: str
    role: str
    profile_image: Optional[str] = None
    phone_number: Optional[str] = None
    bio: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class AdminProfileUpdate(BaseModel):
    name: Optional[str] = None
    profile_image: Optional[str] = None
    phone_number: Optional[str] = None
    bio: Optional[str] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None

class MeResponse(BaseModel):
    name: Optional[str] = None
    email: str
    role: str
    profile_image: Optional[str] = None
    phone_number: Optional[str] = None
    bio: Optional[str] = None
    permissions: list[str]
    model_config = ConfigDict(from_attributes=True)

class RoleCreate(BaseModel):
    name: str
    label: Optional[str] = None
    modules: list[Union[str, dict]] = []

class RoleUpdate(BaseModel):
    label: Optional[str] = None
    modules: Optional[list[Union[str, dict]]] = None

class RoleResponse(BaseModel):
    name: str
    label: Optional[str] = None
    modules: list[Union[str, dict]]
    model_config = ConfigDict(from_attributes=True)

class AssignRoleRequest(BaseModel):
    """Request to assign a role to a user"""
    user_id: int
    role: str

class AdminUserWithRoleResponse(BaseModel):
    """Admin user response with role info"""
    id: int
    name: Optional[str] = None
    email: str
    role: str
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

# ---------- Login ----------
class LoginRequest(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

# ---------- Page ----------
class PageCreate(BaseModel):
    slug: str
    title: str
    meta_data: Optional[dict] = None

class PageResponse(BaseModel):
    id: int
    slug: str
    title: str
    meta_data: Optional[dict] = None
    model_config = ConfigDict(from_attributes=True)

# ---------- Content Block ----------
class ContentBlockCreate(BaseModel):
    page_id: int
    block_key: str
    block_type: str
    content: dict
    order_index: int = 0
    is_published: bool = True

class ContentBlockResponse(BaseModel):
    id: int
    page_id: int
    block_key: str
    block_type: str
    content: dict
    order_index: int
    model_config = ConfigDict(from_attributes=True)
    is_published: bool     

# ---------- Site Setting ----------
class SiteSettingUpdate(BaseModel):
    value: dict

class SiteSettingResponse(BaseModel):
    key: str
    value: dict
    model_config = ConfigDict(from_attributes=True)

# ---------- Brand Assets ----------
class SocialMediaLinks(BaseModel):
    twitter: Optional[str] = None
    linkedin: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    youtube: Optional[str] = None
    whatsapp: Optional[str] = None


# ---------- Site Identity ----------
class SiteIdentity(BaseModel):
    site_name: str
    tagline: Optional[str] = None
    contact_form_notification_email: EmailStr
    admin_email: EmailStr
    timezone: str = "UTC"
    language: str = "en-US"
    logo_url: Optional[str] = None
    logo_public_id: Optional[str] = None
    favicon_url: Optional[str] = None
    favicon_public_id: Optional[str] = None
    social_media: Optional[SocialMediaLinks] = None

class SiteIdentityResponse(BaseModel):
    site_name: str
    tagline: Optional[str] = None
    contact_form_notification_email: EmailStr
    admin_email: EmailStr
    timezone: str
    language: str
    logo_url: Optional[str] = None
    logo_public_id: Optional[str] = None
    favicon_url: Optional[str] = None
    favicon_public_id: Optional[str] = None
    social_media: Optional[SocialMediaLinks] = None

class BrandAssets(BaseModel):
    logo_url: Optional[str] = None
    logo_public_id: Optional[str] = None
    favicon_url: Optional[str] = None
    favicon_public_id: Optional[str] = None
    social_media: Optional[SocialMediaLinks] = None

class BrandAssetsResponse(BaseModel):
    logo_url: Optional[str] = None
    logo_public_id: Optional[str] = None
    favicon_url: Optional[str] = None
    favicon_public_id: Optional[str] = None
    social_media: Optional[SocialMediaLinks] = None
    
    
class MessageCreate(BaseModel):
    name: str
    email: str
    services: Optional[ServiceType] = None
    message: str

class MessageResponse(BaseModel):
    id: int
    name: str
    email: str
    services: Optional[ServiceType] = None
    message: str
    status: str
    created_at: Any
    model_config = ConfigDict(from_attributes=True)

class ReplyRequest(BaseModel):
    reply_message: str
    
class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., alias="newPassword")
    model_config = ConfigDict(populate_by_name=True)


# ---------- Notifications ----------
class NotificationCreate(BaseModel):
    user_id: str
    resource_type: str
    resource_id: Optional[str] = None
    type: str = "system"
    title: str
    message: Optional[str] = None
    data: Optional[dict] = None


class NotificationOut(BaseModel):
    id: str
    user_id: str
    resource_type: str
    resource_id: Optional[str] = None
    type: str
    title: str
    message: Optional[str] = None
    data: Optional[dict] = None
    is_read: bool
    created_at: Any
    read_at: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    items: list[NotificationOut]
    total: int
    unread_count: int
    page: int
    page_size: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class MarkReadRequest(BaseModel):
    ids: Optional[list[str]] = None
    
    
    
    
class ContactFormIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: str = Field(..., min_length=1, max_length=20)
    subject: str = Field(..., min_length=1, max_length=500)
    message: str = Field(..., min_length=1)


class ContactFormOut(BaseModel):
    ticket_id: int
    status: str


class XlsxImportResponse(BaseModel):
    id: int
    filename: str
    sheet_names: Optional[list] = None
    headers: list
    row_count: int
    status: str
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
    
    
# app/schema.py

class LeadsResponse(BaseModel):
    id: int
    username: str
    phone: str
    city: str
    email: Optional[EmailStr] = None
    service_type: Optional[ServiceType] = None
    cnic: Optional[str] = None
    status: Optional[str] = None
    created_at: datetime
 
    class Config:
        from_attributes = True
 
    
class LeadExportRequest(BaseModel):
    format: Literal["csv", "excel", "pdf"] = "csv"
    lead_ids: list[int] | None = None
    select_all: bool = False
    service_type: ServiceType | None = None
    fields: list[str] | None = None  # if None, export all fields
    
class LeadStep1Create(BaseModel):
    """Step 1 of the form: name, phone, city."""
    username: str
    phone: str
    city: str
 
 
class LeadStep2Update(BaseModel):
    """Step 2 of the form: cnic, service, email — all optional so PATCH stays flexible."""
    cnic: Optional[str] = None
    service_type: Optional[ServiceType] = None
    email: Optional[EmailStr] = None



class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    phone: str
 
    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v
 
 
class UserOut(BaseModel):
    id: int
    email: EmailStr
    username: str
    phone: str
    created_at: datetime
    slug: str = "user"
 
    class Config:
        from_attributes = True
 
 
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    slug: str = "user"
    redirect_to: str = "/user-dashboard"
 