import datetime
import enum
from datetime import timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    TIMESTAMP,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .db import Base
from .enums import ServiceType
from enum import Enum as PyEnum

class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    email = Column(String(255), unique=True, index=True)
    password_hash = Column(String(255))
    role = Column(String(50), default="admin")
    profile_image = Column(String(1000), nullable=True)
    phone_number = Column(String(50), nullable=True)
    bio = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

class Role(Base):
    __tablename__ = "roles"
    name = Column(String(50), primary_key=True, index=True)
    label = Column(String(255), nullable=True)
    modules = Column(JSON, nullable=False, default=list)

class Page(Base):
    __tablename__ = "pages"
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String(100), unique=True, index=True)
    title = Column(String(255))
    meta_data = Column(JSON)

class ContentBlock(Base):
    __tablename__ = "content_blocks"
    id = Column(Integer, primary_key=True, index=True)
    page_id = Column(Integer, ForeignKey("pages.id"))
    block_key = Column(String(100))
    block_type = Column(String(50))
    content = Column(JSON)
    order_index = Column(Integer, default=0)
    is_published = Column(Boolean, default=True) 

class SiteSetting(Base):
    __tablename__ = "site_settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True)
    value = Column(JSON)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

class Media(Base):
    __tablename__ = "media"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255))
    url = Column(String(500))
    public_id = Column(String(500), nullable=True)
    resource_type = Column(String(50), nullable=True)
    format = Column(String(50), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    bytes = Column(Integer, nullable=True)
    uploaded_at = Column(TIMESTAMP, server_default=func.now())
    
class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    email = Column(String(255))
    services = Column(SAEnum(ServiceType), nullable=True)
    message = Column(String(2000))
    status = Column(String(50), default="new")
    created_at = Column(TIMESTAMP, server_default=func.now())
    
    
    
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255))
    token = Column(String(255), unique=True)
    expires_at = Column(TIMESTAMP)
    used = Column(Boolean, default=False)


# Notifications table integrated into main DB
class NotificationType(PyEnum):
    created = "created"
    updated = "updated"
    deleted = "deleted"
    system = "system"


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(128), index=True, nullable=False)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(String(64), nullable=True)
    type = Column(SAEnum(NotificationType), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(String, nullable=True)
    data = Column(JSON, nullable=True)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_notifications_user_is_read", "user_id", "is_read"),
    )
    
    
    
    
    
def utcnow():
    return datetime.datetime.now(timezone.utc)


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"   # from customer
    OUTBOUND = "outbound"  # from admin


class SupportCustomer(Base):
    __tablename__ = "support_customers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(320), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    tickets = relationship("SupportTicket", back_populates="customer")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("support_customers.id"), nullable=False)
    subject = Column(String(500), nullable=False)
    status = Column(SAEnum(TicketStatus), default=TicketStatus.OPEN, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer = relationship("SupportCustomer", back_populates="tickets")
    messages = relationship(
        "SupportTicketMessage", back_populates="ticket", order_by="SupportTicketMessage.created_at"
    )
    @property
    def reply_to_address(self) -> str:
        from app.support_system.config import settings

        # Use the configured IMAP mailbox email for Reply-To. This ensures the
        # user sees a real support address and avoids incorrect ticket+... bounces.
        # If IMAP_USER is missing, fall back to the SMTP user.
        return settings.IMAP_USER or settings.SMTP_USER


class SupportTicketMessage(Base):
    __tablename__ = "support_ticket_messages"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id"), nullable=False)
    direction = Column(SAEnum(MessageDirection), nullable=False)
    sender_email = Column(String(320), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    ticket = relationship("SupportTicket", back_populates="messages")


class ImportStatus(str, enum.Enum):
    STAGED = "staged"
    VALIDATED = "validated"
    IMPORTED = "imported"
    FAILED = "failed"


class XlsxImport(Base):
    __tablename__ = "xlsx_imports"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    sheet_names = Column(JSON, nullable=True)
    headers = Column(JSON, nullable=False)
    rows = Column(JSON, nullable=False)
    row_count = Column(Integer, default=0)
    status = Column(SAEnum(ImportStatus), default=ImportStatus.STAGED, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    error_message = Column(Text, nullable=True)



class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), nullable=False)
    email = Column(String(320), nullable=True)
    phone = Column(String(20), nullable=False)
    service_type = Column(
        SAEnum(ServiceType, values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=True,
    )
    city = Column(String(100), nullable=False)
    cnic = Column(String, nullable=True)
    status = Column(String, nullable=True, default="partial")
    created_at = Column(DateTime(timezone=False), default=utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), nullable=False, unique=True)
    email = Column(String(320), nullable=True, unique=True)
    phone = Column(String(20), nullable=True, unique=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=False), default=utcnow)