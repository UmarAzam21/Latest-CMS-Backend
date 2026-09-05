from sqlalchemy.orm import Session
from sqlalchemy import desc
from fastapi import HTTPException, status

from app import models
from app.enums import ModuleAccess


def _check_access(current_admin: dict, required_access: ModuleAccess):
    """Helper function to check access level for CRUD operations."""
    if current_admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    
    modules = current_admin.get("modules", [])
    
    # Admin users with "*" have full access
    if "*" in modules:
        return
    
    # Check for support_system module access
    for module in modules:
        if isinstance(module, dict):
            if "support_system" in module:  
                user_access = module["support_system"].lower()
                required = required_access.value.lower()
                
                if user_access == "all" or user_access == required or (user_access == "update" and required == "read"):
                    return
        elif module == "support_system":
            # Backward compatible: module in list = UPDATE access
            if required_access == ModuleAccess.UPDATE or required_access == ModuleAccess.ALL:
                return
            elif required_access == ModuleAccess.READ:
                return
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Access denied. Required {required_access.value} access to support_system module.",
    )


def get_or_create_customer(db: Session, email: str, name: str | None = None, phone: str | None = None, current_admin: dict = None) -> models.SupportCustomer:
    # Check READ access for get_or_create_customer
    if current_admin:
        _check_access(current_admin, ModuleAccess.READ)
    
    customer = db.query(models.SupportCustomer).filter(models.SupportCustomer.email == email).first()
    if customer:
        if name and not customer.name:
            customer.name = name
        if phone and not customer.phone:
            customer.phone = phone
        if (name and not customer.name) or (phone and not customer.phone):
            db.commit() 
            db.refresh(customer)
        return customer

    customer = models.SupportCustomer(email=email, name=name, phone=phone)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def create_ticket_with_message(
    db: Session, customer: models.SupportCustomer, subject: str, body: str, current_admin: dict = None
) -> models.SupportTicket:
    # Check UPDATE access for create_ticket_with_message
    if current_admin:
        _check_access(current_admin, ModuleAccess.UPDATE)
    
    ticket = models.SupportTicket(customer_id=customer.id, subject=subject)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    message = models.SupportTicketMessage(
        ticket_id=ticket.id,
        direction=models.MessageDirection.INBOUND,
        sender_email=customer.email,
        body=body,
    )
    db.add(message)
    db.commit()
    db.refresh(ticket)
    return ticket


def add_message(
    db: Session, ticket: models.SupportTicket, direction: models.MessageDirection,
    sender_email: str, body: str, current_admin: dict = None
) -> models.SupportTicketMessage:
    # Check UPDATE access for add_message
    if current_admin:
        _check_access(current_admin, ModuleAccess.UPDATE)
    
    message = models.SupportTicketMessage(
        ticket_id=ticket.id,
        direction=direction,
        sender_email=sender_email,
        body=body,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def list_tickets(db: Session, current_admin: dict = None):
    # Check READ access for list_tickets
    if current_admin:
        _check_access(current_admin, ModuleAccess.READ)
    
    return (
        db.query(models.SupportTicket)
        .order_by(desc(models.SupportTicket.updated_at))
        .all()
    )


def get_ticket(db: Session, ticket_id: int, current_admin: dict = None) -> models.SupportTicket | None:
    # Check READ access for get_ticket
    if current_admin:
        _check_access(current_admin, ModuleAccess.READ)
    
    return db.query(models.SupportTicket).filter(models.SupportTicket.id == ticket_id).first()
