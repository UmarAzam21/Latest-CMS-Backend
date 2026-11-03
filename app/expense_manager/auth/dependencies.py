from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError
from sqlalchemy.orm import Session

from ...auth import decode_access_token
from ...db import get_db
from ...models import ExpenseManagerAuth, ExpenseManagerBusiness, User
from ...user_account.dependencies import get_current_user
from ...user_account.dependencies import bearer_scheme


def get_current_expense_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    current_user.current_business_id = None
    try:
        payload = decode_access_token(credentials.credentials)
        if payload.get("scope") != "expense_manager":
            return current_user
        account = db.query(ExpenseManagerAuth).filter(
            ExpenseManagerAuth.user_id == current_user.id
        ).first()
        if account is None or not account.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No active expense manager account is linked to this user.",
            )

        business_id = payload.get("business_id")
        query = db.query(ExpenseManagerBusiness).filter(
            ExpenseManagerBusiness.account_id == account.id
        )
        if business_id is None:
            business = query.order_by(ExpenseManagerBusiness.id).first()
        else:
            try:
                business_id = int(business_id)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication token.",
                )
            business = query.filter(ExpenseManagerBusiness.id == business_id).first()
        if business is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The selected business does not belong to this account.",
            )
        current_user.current_business_id = business.id
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )
    return current_user


def get_current_expense_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExpenseManagerAuth:
    account = db.query(ExpenseManagerAuth).filter(
        ExpenseManagerAuth.user_id == current_user.id
    ).first()
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No expense manager account is linked to this user.",
        )
    if not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This expense manager account is inactive.",
        )
    return account
