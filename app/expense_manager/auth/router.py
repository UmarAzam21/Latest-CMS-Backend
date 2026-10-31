from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import ExpenseManagerAuth
from ...schema import (
    ExpenseManagerAuthResponse,
    ExpenseManagerChangePinRequest,
    ExpenseManagerLoginRequest,
    ExpenseManagerRegisterRequest,
    ExpenseManagerTokenResponse,
)
from . import service
from .dependencies import get_current_expense_account

router = APIRouter(prefix="/expenses/auth", tags=["Expense Manager Auth"])


@router.post("/register", response_model=ExpenseManagerTokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: ExpenseManagerRegisterRequest, db: Session = Depends(get_db)):
    account = service.register_account(db, request)
    return {"access_token": service.create_token_for(account), "account": account}


@router.post("/login", response_model=ExpenseManagerTokenResponse)
def login(request: ExpenseManagerLoginRequest, db: Session = Depends(get_db)):
    account = service.authenticate(db, request.phone_number, request.pin)
    return {"access_token": service.create_token_for(account), "account": account}


@router.get("/me", response_model=ExpenseManagerAuthResponse)
def get_me(account: ExpenseManagerAuth = Depends(get_current_expense_account)):
    return account


@router.post("/change-pin", status_code=status.HTTP_204_NO_CONTENT)
def change_pin(
    request: ExpenseManagerChangePinRequest,
    db: Session = Depends(get_db),
    account: ExpenseManagerAuth = Depends(get_current_expense_account),
):
    service.change_pin(db, account, request.current_pin, request.new_pin)
