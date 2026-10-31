from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import ExpenseManagerAuth, User
from ...user_account.dependencies import get_current_user


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
