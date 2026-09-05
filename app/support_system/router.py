from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.auth import get_current_admin, require_module_access
from app.enums import ModuleAccess
from app.schema import ContactFormIn, ContactFormOut
from app.support_system import crud
from app.support_system.config import settings
from app.support_system.email_utils import send_email
from app.support_system.imap_worker import process_mailbox
from app.router import notify_admin_event

router = APIRouter()
templates = None


def _safe_status(val):
    return getattr(val, "value", str(val))


def _serialize_message(msg):
    return {
        "id": msg.id,
        "direction": getattr(msg.direction, "value", str(msg.direction)),
        "sender_email": msg.sender_email,
        "body": msg.body,
        "created_at": msg.created_at.isoformat() if getattr(msg, "created_at", None) else None,
    }


def _serialize_ticket(ticket):
    return {
        "id": ticket.id,
        "subject": ticket.subject,
        "status": _safe_status(ticket.status),
        "customer": {
            "id": ticket.customer.id if ticket.customer else None,
            "email": ticket.customer.email if ticket.customer else None,
            "name": ticket.customer.name if ticket.customer else None,
        },
        "created_at": ticket.created_at.isoformat() if getattr(ticket, "created_at", None) else None,
        "updated_at": ticket.updated_at.isoformat() if getattr(ticket, "updated_at", None) else None,
        "messages": [_serialize_message(m) for m in getattr(ticket, "messages", [])],
    }


def require_admin() -> str:
    return "admin"


@router.post("/contact", response_model=ContactFormOut)
async def submit_contact_form(payload: ContactFormIn, db: Session = Depends(get_db)):
    customer = crud.get_or_create_customer(db, email=payload.email, name=payload.name, phone=payload.phone)
    ticket = crud.create_ticket_with_message(
        db, customer=customer, subject=payload.subject, body=payload.message
    )

    try:
        send_email(
            to_address=customer.email,
            subject=f"[Ticket #{ticket.id}] {ticket.subject}",
            body=(
                f"Hi {customer.name or ''},\n\n"
                "Thanks for reaching out — we've received your message and "
                "will get back to you soon.\n\n"
                f"Your message:\n{payload.message}\n\n"
                "— Support Team"
            ),
            reply_to=ticket.reply_to_address,
        )
    except Exception:
        pass

    await notify_admin_event(
        None,
        db,
        "support_ticket",
        str(ticket.id),
        "New support message received",
        f"New support message from {customer.name or customer.email}.",
        "created",
        {"ticket_id": ticket.id, "email": customer.email},
    )

    return ContactFormOut(ticket_id=ticket.id, status=ticket.status.value)

# ---------------------------------------------------------------------------
# Admin dashboard
# ---------------------------------------------------------------------------

@router.get("/admin")
def admin_dashboard(
    request: Request, db: Session = Depends(get_db), current_admin: dict = Depends(require_module_access("support_system", ModuleAccess.READ))
):
    tickets = crud.list_tickets(db, current_admin=current_admin)
    return [ _serialize_ticket(t) for t in tickets ]


@router.get("/admin/tickets/{ticket_id}")
def admin_ticket_detail(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module_access("support_system", ModuleAccess.READ)),
):
    ticket = crud.get_ticket(db, ticket_id, current_admin=current_admin)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _serialize_ticket(ticket)


@router.get("/admin/tickets/{ticket_id}/reply-to")
def admin_ticket_reply_to(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module_access("support_system", ModuleAccess.READ)),
):
    ticket = crud.get_ticket(db, ticket_id, current_admin=current_admin)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ticket_id": ticket_id, "reply_to": ticket.reply_to_address}


@router.post("/admin/tickets/{ticket_id}/reply")
def admin_reply(
    ticket_id: int,
    body: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module_access("support_system", ModuleAccess.UPDATE)),
):
    ticket = crud.get_ticket(db, ticket_id, current_admin=current_admin)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    try:
        send_email(
            to_address=ticket.customer.email,
            subject=f"Re: [Ticket #{ticket.id}] {ticket.subject}",
            body=body,
            reply_to=ticket.reply_to_address,
        )
    except Exception:
        pass

    crud.add_message(
        db,
        ticket=ticket,
        direction=models.MessageDirection.OUTBOUND,
        sender_email=settings.SMTP_USER or current_admin.get("email", "admin"),
        body=body,
        current_admin=current_admin,
    )
    return {"status": "sent", "ticket_id": ticket_id}


@router.post("/admin/tickets/{ticket_id}/close")
def admin_close_ticket(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module_access("support_system", ModuleAccess.UPDATE)),
):
    ticket = crud.get_ticket(db, ticket_id, current_admin=current_admin)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket.status = models.TicketStatus.CLOSED
    db.commit()
    db.refresh(ticket)

    return {"status": "closed", "ticket_id": ticket_id, "ticket_status": ticket.status.value}


# Development-only helper: simulate an inbound reply (useful when IMAP isn't available)
@router.post("/admin/tickets/{ticket_id}/simulate-reply")
def simulate_reply(
    ticket_id: int,
    sender_email: str = Form(...),
    body: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: dict = Depends(require_module_access("support_system", ModuleAccess.UPDATE)),
):
    ticket = crud.get_ticket(db, ticket_id, current_admin=current_admin)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    msg = crud.add_message(
        db,
        ticket=ticket,
        direction=models.MessageDirection.INBOUND,
        sender_email=sender_email,
        body=body,
        current_admin=current_admin,
    )

    return {"status": "simulated", "message": _serialize_message(msg), "ticket": _serialize_ticket(ticket)}


# Admin endpoint to trigger IMAP processing once. Use `sync=true` to run synchronously.
@router.post("/admin/process-imap")
def trigger_imap_processing(
    background_tasks: BackgroundTasks,
    sync: bool = False,
    current_admin: dict = Depends(require_module_access("support_system", ModuleAccess.UPDATE)),
):
    if sync:
        try:
            count = process_mailbox()
            return {"status": "processed", "count": count}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    else:
        background_tasks.add_task(process_mailbox)
        return {"status": "queued"}
