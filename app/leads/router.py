import io
import csv
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from ..db import get_db
from ..models import Lead, AdminUser
from ..schema import LeadStep1Create, LeadStep2Update, LeadsResponse, LeadExportRequest
from ..router import notify, resolve_notification_user_id  # this is app/router.py, one level up from app/leads/

router = APIRouter()

FIELD_MAP = {
    "id": ("ID", lambda l: l.id),
    "username": ("Name", lambda l: l.username),
    "email": ("Email", lambda l: l.email),
    "phone": ("Phone", lambda l: l.phone),
    "cnic": ("CNIC", lambda l: l.cnic),
    "service_type": ("Service Type", lambda l: l.service_type.value if hasattr(l.service_type, "value") else l.service_type),
    "city": ("City", lambda l: l.city),
    "status": ("Status", lambda l: l.status),
    "created_at": ("Created At", lambda l: l.created_at.isoformat() if l.created_at else ""),
}


@router.post("/api/admin/leads/step1", response_model=LeadsResponse)
async def create_lead_step1(
    payload: LeadStep1Create,
    db: Session = Depends(get_db),
):
    """
    Called when the user clicks 'Next' on step 1 of the frontend form.
    Saves a partial lead immediately so we still have a callable lead
    even if the user never finishes step 2.
    """
    new_lead = Lead(
        username=payload.username,
        phone=payload.phone,
        city=payload.city,
        status="partial",
    )

    db.add(new_lead)
    db.commit()
    db.refresh(new_lead)

    admin_user = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
    await notify(
        user_id=resolve_notification_user_id(
            {"email": admin_user.email} if admin_user and admin_user.email else None, db=db
        ),
        resource_type="lead",
        resource_id=str(new_lead.id),
        type_="created",
        title="New partial lead",
        message=f"Lead started by {new_lead.username} ({new_lead.phone}) — step 1 only",
        db=db,
    )

    return new_lead


@router.patch("/api/admin/leads/{lead_id}/step2", response_model=LeadsResponse)
async def complete_lead_step2(
    lead_id: int,
    payload: LeadStep2Update,
    db: Session = Depends(get_db),
):
    """
    Called when the user submits step 2 (cnic, service, email).
    Fills in the remaining fields on the lead created in step 1.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if payload.cnic is not None:
        lead.cnic = payload.cnic
    if payload.service_type is not None:
        lead.service_type = payload.service_type
    if payload.email is not None:
        lead.email = payload.email

    lead.status = "completed"

    db.commit()
    db.refresh(lead)

    admin_user = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
    await notify(
        user_id=resolve_notification_user_id(
            {"email": admin_user.email} if admin_user and admin_user.email else None, db=db
        ),
        resource_type="lead",
        resource_id=str(lead.id),
        type_="updated",
        title="Lead completed",
        message=f"Lead {lead.username} finished step 2 ({lead.email})",
        db=db,
    )

    return lead


@router.patch("/api/admin/leads/{lead_id}", response_model=LeadsResponse)
async def update_lead(
    lead_id: int,
    lead_update: LeadsResponse,
    db: Session = Depends(get_db),
):
    """Full overwrite — used by any admin screen that edits a lead's every field at once."""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    lead.username = lead_update.username
    lead.email = lead_update.email
    lead.phone = lead_update.phone
    lead.service_type = lead_update.service_type
    lead.city = lead_update.city
    # NOTE: cnic is intentionally NOT touched here. If you want this endpoint
    # to also update cnic, add: lead.cnic = lead_update.cnic
    # But since LeadsResponse now has cnic as Optional, if you DO add that line
    # and the caller doesn't send cnic, it'll get wiped to None. Use the same
    # "if not None" pattern as step2 above if that's a risk for you.

    db.commit()
    db.refresh(lead)

    admin_user = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
    await notify(
        user_id=resolve_notification_user_id(
            {"email": admin_user.email} if admin_user and admin_user.email else None, db=db
        ),
        resource_type="lead",
        resource_id=str(lead.id),
        type_="updated",
        title="Lead updated",
        message=f"Lead {lead.username} ({lead.email}) was updated",
        db=db,
    )

    return lead


@router.get("/api/admin/leads", response_model=list[LeadsResponse])
async def get_leads(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 50,
):
    leads = (
        db.query(Lead)
        .order_by(Lead.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return leads


@router.post("/api/admin/leads", response_model=LeadsResponse)
async def create_lead(
    message: LeadsResponse,
    db: Session = Depends(get_db),
):
    """Full one-shot create — kept for any caller that still submits everything at once."""
    new_lead = Lead(
        username=message.username,
        email=message.email,
        phone=message.phone,
        service_type=message.service_type,
        city=message.city,
        status="completed",
    )

    db.add(new_lead)
    db.commit()
    db.refresh(new_lead)

    admin_user = db.query(AdminUser).order_by(AdminUser.id.asc()).first()
    await notify(
        user_id=resolve_notification_user_id(
            {"email": admin_user.email} if admin_user and admin_user.email else None, db=db
        ),
        resource_type="lead",
        resource_id=str(new_lead.id),
        type_="created",
        title="New lead received",
        message=f"Lead from {new_lead.username} ({new_lead.email})",
        db=db,
    )

    return new_lead


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


@router.post("/api/admin/leads/download")
async def download_leads(
    payload: LeadExportRequest,
    db: Session = Depends(get_db),
    # admin: AdminUser = Depends(get_current_admin_user),
):
    leads = get_leads_for_export(db, payload)
    fields = resolve_fields(payload.fields)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if payload.format == "csv":
        buffer = build_csv(leads, fields)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=leads_{timestamp}.csv"},
        )

    if payload.format == "excel":
        buffer = build_excel(leads, fields)
        return StreamingResponse(
            buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=leads_{timestamp}.xlsx"},
        )

    if payload.format == "pdf":
        buffer = build_pdf(leads, fields)
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=leads_{timestamp}.pdf"},
        )