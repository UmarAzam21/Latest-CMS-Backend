import asyncio
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import decode_access_token
from app.db import Base
from app.enums import CardColor
from app.expense_manager.auth import service
from app.expense_manager.auth.dependencies import get_current_expense_user
from app.expense_manager.router import (
    create_card,
    create_expense,
    feed_expense_categories,
    get_cards,
    get_transactions,
)
from app.models import ExpenseManagerAuth, ExpenseManagerBusiness, User
from app.schema import ExpenseManagerBusinessCreateRequest, ExpenseManagerRegisterRequest


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def expense_user(db: Session, account: ExpenseManagerAuth, business_id: int) -> User:
    token = service.create_token_for(account, business_id)
    return get_current_expense_user(
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        current_user=db.query(User).filter(User.id == account.user_id).one(),
        db=db,
    )


def test_business_creation_and_switching_scopes_expense_data(db: Session):
    account = service.register_account(
        db,
        ExpenseManagerRegisterRequest(
            phone_number="03001234567",
            business_name="My Expenses",
            pin="1234",
        ),
    )
    first_business = db.query(ExpenseManagerBusiness).filter(
        ExpenseManagerBusiness.account_id == account.id
    ).one()
    second_business = service.create_business(
        db,
        account,
        ExpenseManagerBusinessCreateRequest(
            business_name="Ali azhar",
            business_type="household",
            currency="PKR",
        ),
    )

    second_token = service.create_token_for(account, second_business.id)
    assert decode_access_token(second_token)["business_id"] == second_business.id
    second_user = expense_user(db, account, second_business.id)
    second_card = asyncio.run(
        create_card(
            card_number="1234",
            bank_name=None,
            balance=Decimal("100.00"),
            card_color=None,
            expiry_month=None,
            expiry_year=None,
            db=db,
            current_user=second_user,
        )
    )
    asyncio.run(feed_expense_categories(db=db, current_user=second_user))
    asyncio.run(
        create_expense(
            subject="Household purchase",
            category="Other",
            amount=Decimal("10.00"),
            entry_date=date.today(),
            account_id=second_card.id,
            description=None,
            db=db,
            current_user=second_user,
        )
    )
    assert len(asyncio.run(get_transactions(db=db, current_user=second_user))) == 1

    first_user = expense_user(db, account, first_business.id)
    assert asyncio.run(get_cards(db=db, current_user=first_user)) == []
    assert asyncio.run(get_transactions(db=db, current_user=first_user)) == []

    first_card = asyncio.run(
        create_card(
            card_number="1234",
            bank_name=None,
            balance=Decimal("0"),
            card_color=CardColor.Primary,
            expiry_month=None,
            expiry_year=None,
            db=db,
            current_user=first_user,
        )
    )
    assert first_card.id != second_card.id

    other_account = service.register_account(
        db,
        ExpenseManagerRegisterRequest(
            phone_number="03007654321",
            business_name="Other account",
            pin="5678",
        ),
    )
    with pytest.raises(HTTPException) as error:
        service.get_business(db, other_account, second_business.id)
    assert error.value.status_code == 404

    with pytest.raises(HTTPException) as error:
        expense_user(db, other_account, second_business.id)
    assert error.value.status_code == 403
