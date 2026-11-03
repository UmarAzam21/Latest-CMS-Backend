from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import ExpenseManagerAuth, ExpenseManagerBusiness, User
from ...schema import (
    ExpenseManagerAuthResponse,
    ExpenseManagerBusinessesResponse,
    ExpenseManagerBusinessCreateRequest,
    ExpenseManagerChangePinRequest,
    ExpenseManagerLoginRequest,
    ExpenseManagerRegisterRequest,
    ExpenseManagerTokenResponse,
)
from . import service
from .dependencies import get_current_expense_account, get_current_expense_user

router = APIRouter(prefix="/expenses/auth", tags=["Expense Manager Auth"])


def _business_response(business: ExpenseManagerBusiness) -> dict:
    return {
        "id": business.id,
        "business_name": business.name,
        "business_type": business.business_type,
        "currency": business.currency,
        "created_at": business.created_at,
    }


def _token_response(
    account: ExpenseManagerAuth,
    business: ExpenseManagerBusiness,
    db: Session,
) -> ExpenseManagerTokenResponse:
    businesses = service.list_businesses(db, account)
    return ExpenseManagerTokenResponse(
        access_token=service.create_token_for(account, business.id),
        account=account,
        active_business_id=business.id,
        businesses=[_business_response(item) for item in businesses],
    )


@router.post("/register", response_model=ExpenseManagerTokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: ExpenseManagerRegisterRequest, db: Session = Depends(get_db)):
    account = service.register_account(db, request)
    business = service.ensure_initial_business(db, account)
    return _token_response(account, business, db)


@router.post("/login", response_model=ExpenseManagerTokenResponse)
def login(request: ExpenseManagerLoginRequest, db: Session = Depends(get_db)):
    account = service.authenticate(db, request.phone_number, request.pin)
    business = service.ensure_initial_business(db, account)
    return _token_response(account, business, db)


@router.get("/me", response_model=ExpenseManagerAuthResponse)
def get_me(account: ExpenseManagerAuth = Depends(get_current_expense_account)):
    return account


@router.get("/businesses", response_model=ExpenseManagerBusinessesResponse)
def get_businesses(
    db: Session = Depends(get_db),
    account: ExpenseManagerAuth = Depends(get_current_expense_account),
    current_user: User = Depends(get_current_expense_user),
):
    businesses = service.list_businesses(db, account)
    return {
        "active_business_id": current_user.current_business_id or businesses[0].id,
        "businesses": [_business_response(business) for business in businesses],
    }


@router.post(
    "/businesses",
    response_model=ExpenseManagerTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_business(
    request: ExpenseManagerBusinessCreateRequest,
    db: Session = Depends(get_db),
    account: ExpenseManagerAuth = Depends(get_current_expense_account),
):
    business = service.create_business(db, account, request)
    return _token_response(account, business, db)


@router.post(
    "/businesses/{business_id}/switch",
    response_model=ExpenseManagerTokenResponse,
)
def switch_business(
    business_id: int,
    db: Session = Depends(get_db),
    account: ExpenseManagerAuth = Depends(get_current_expense_account),
):
    business = service.get_business(db, account, business_id)
    return _token_response(account, business, db)


@router.post("/change-pin", status_code=status.HTTP_204_NO_CONTENT)
def change_pin(
    request: ExpenseManagerChangePinRequest,
    db: Session = Depends(get_db),
    account: ExpenseManagerAuth = Depends(get_current_expense_account),
):
    service.change_pin(db, account, request.current_pin, request.new_pin)
