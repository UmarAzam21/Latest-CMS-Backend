from pydantic import BaseModel, ConfigDict, EmailStr, Field,field_validator
from typing import Optional, Any, Union
from datetime import date, datetime
from decimal import Decimal
import uuid
from .enums import ServiceType, ExpenseType, DebtCategory, ExpenseCategory, ExpenseCategoryColor, ExpenseAccountType
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


class IncomeExpenseSummaryResponse(BaseModel):
    id: int
    expense_type: ExpenseType
    subject: str
    category: str
    amount: Decimal
    entry_date: date
    account_id: Optional[int] = None
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ExpenseCategorySummaryResponse(BaseModel):
    categories: list[ExpenseCategory]
    colors: list[ExpenseCategoryColor]


class ExpenseCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    color: ExpenseCategoryColor
    type: ExpenseType


class ExpenseCategoryOptionResponse(BaseModel):
    id: int
    type: ExpenseType
    name: str
    color: str
    is_default: bool

    model_config = ConfigDict(from_attributes=True)


class ExpenseCategoryFeedResponse(BaseModel):
    categories: list[ExpenseCategoryOptionResponse]
    colors: list[ExpenseCategoryColor]


class CardBalanceResponse(BaseModel):
    card_id: int
    balance: Decimal


class CardResponse(BaseModel):
    id: int
    # card_type: ExpenseAccountType
    card_number: str
    bank_name: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
    balance: Decimal = 0


# AccountBalanceResponse = CardBalanceResponse
# AccountResponse = CardResponse


# CARD_TABLE_SKETCH = """
#     **cards**
# | column | type | notes |
# |---|---|---|
# | id | uuid PK | |
# | user_id | uuid FK | |
# | label | text | |
# | balance | numeric(12,2) | |
# | last4 | varchar(4) | |
# | expiry_month | int | |
# | expiry_year | int | |
# | gradient | enum(primary,secondary,dark) | |
# """

class Debt(BaseModel):
    id: int
    subject: str
    amount: Decimal
    card_id: Optional[int] = None
    category1: DebtCategory
    entry_date: date
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AgentsLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AgentsRegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["admin", "agent"]


class AgentsLoginResponse(BaseModel):
    message: str
    token: str
    token_type: str = "bearer"
    slug: Literal["user", "admin"]
    redirect_to: str
    profile: dict[str, Any]


class AgentsRegisterResponse(BaseModel):
    message: str
    agent_id: int
    username: str
    role: Literal["admin", "agent"]

# ---------- Expense Manager Auth ----------
def _normalize_phone_number(value: str) -> str:
    phone = "".join(ch for ch in value.strip() if ch not in " -()")
    digits = phone[1:] if phone.startswith("+") else phone
    if not digits.isdigit() or not 7 <= len(digits) <= 15:
        raise ValueError("Enter a valid phone number (7-15 digits, optional leading +).")
    return phone


class ExpenseManagerRegisterRequest(BaseModel):
    phone_number: str
    business_name: str = Field(..., min_length=1, max_length=255)
    business_type: str = Field("personal", min_length=1, max_length=50)
    currency: str = Field("PKR", pattern=r"^[A-Za-z]{3}$")
    email: Optional[EmailStr] = None
    pin: str = Field(..., pattern=r"^\d{4,6}$")

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        return _normalize_phone_number(v)

    @field_validator("business_name")
    @classmethod
    def strip_business_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Business name cannot be empty.")
        return v

    @field_validator("business_type")
    @classmethod
    def normalize_business_type(cls, v: str) -> str:
        normalized = v.strip().lower()
        if not normalized:
            raise ValueError("Business type cannot be empty.")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.upper()


class ExpenseManagerBusinessCreateRequest(BaseModel):
    business_name: str = Field(..., min_length=1, max_length=255)
    business_type: str = Field("business", min_length=1, max_length=50)
    currency: str = Field("PKR", pattern=r"^[A-Za-z]{3}$")

    @field_validator("business_name")
    @classmethod
    def strip_business_name(cls, v: str) -> str:
        normalized = v.strip()
        if not normalized:
            raise ValueError("Business name cannot be empty.")
        return normalized

    @field_validator("business_type")
    @classmethod
    def normalize_business_type(cls, v: str) -> str:
        normalized = v.strip().lower()
        if not normalized:
            raise ValueError("Business type cannot be empty.")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.upper()


class ExpenseManagerBusinessResponse(BaseModel):
    id: int
    business_name: str
    business_type: str
    currency: str
    created_at: datetime


class ExpenseManagerBusinessesResponse(BaseModel):
    active_business_id: int
    businesses: list[ExpenseManagerBusinessResponse]


class ExpenseManagerLoginRequest(BaseModel):
    phone_number: str
    pin: str = Field(..., pattern=r"^\d{4,6}$")

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        return _normalize_phone_number(v)


class ExpenseManagerChangePinRequest(BaseModel):
    current_pin: str = Field(..., pattern=r"^\d{4,6}$")
    new_pin: str = Field(..., pattern=r"^\d{4,6}$")


class ExpenseManagerAuthResponse(BaseModel):
    id: int
    user_id: int
    phone_number: str
    business_name: str
    email: Optional[EmailStr] = None
    is_active: bool
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ExpenseManagerTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    slug: str = "user"
    redirect_to: str = "/expense-dashboard"
    account: ExpenseManagerAuthResponse
    active_business_id: Optional[int] = None
    businesses: list[ExpenseManagerBusinessResponse] = Field(default_factory=list)
