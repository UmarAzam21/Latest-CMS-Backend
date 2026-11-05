from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..enums import CardColor, ExpenseAccountType,DebtCategory, ExpenseCategory, ExpenseCategoryColor, ExpenseType
from ..models import Card, ExpenseCategoryOption, IncomeExpense, User
from ..schema import (
    CardBalanceResponse,
    CardResponse,
    ExpenseCategoryCreate,
    ExpenseCategoryFeedResponse,
    ExpenseCategoryOptionResponse,
    IncomeExpenseSummaryResponse,
)
from .auth.dependencies import get_current_expense_user
from .notification.config import settings as notification_settings
from .notification.events import NotificationType
from .notification.service import publish

router = APIRouter(prefix="/expenses", tags=["Expenses"])


DEFAULT_CATEGORY_COLORS = {
    ExpenseCategory.utilities.value: ExpenseCategoryColor.Blue,
    ExpenseCategory.food.value: ExpenseCategoryColor.Yellow,
    ExpenseCategory.transport.value: ExpenseCategoryColor.Green,
    ExpenseCategory.rent.value: ExpenseCategoryColor.Red,
    ExpenseCategory.business.value: ExpenseCategoryColor.Purple,
    ExpenseCategory.other.value: ExpenseCategoryColor.Gray,
}


def _get_user_categories(db: Session, current_user: User) -> list[ExpenseCategoryOption]:
    categories = db.query(ExpenseCategoryOption).filter(
        *_user_scope(ExpenseCategoryOption, current_user)
    ).order_by(ExpenseCategoryOption.id).all()

    if not categories:
        db.add_all(
            ExpenseCategoryOption(
                user_id=current_user.id,
                business_id=getattr(current_user, "current_business_id", None),
                name=category.value,
                color=DEFAULT_CATEGORY_COLORS[category.value].value,
                is_default=True,
            )
            for category in ExpenseCategory
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        categories = db.query(ExpenseCategoryOption).filter(
            *_user_scope(ExpenseCategoryOption, current_user)
        ).order_by(ExpenseCategoryOption.id).all()

    return categories


def _user_scope(model, current_user: User):
    business_id = getattr(current_user, "current_business_id", None)
    business_filter = (
        model.business_id == business_id
        if business_id is not None
        else model.business_id.is_(None)
    )
    return model.user_id == current_user.id, business_filter


def _require_user_category(
    db: Session,
    current_user: User,
    name: str,
    category_type: ExpenseType,
) -> str:
    normalized_name = name.strip()
    category = db.query(ExpenseCategoryOption).filter(
        *_user_scope(ExpenseCategoryOption, current_user),
        ExpenseCategoryOption.name == normalized_name,
        ExpenseCategoryOption.type == category_type,
    ).first()
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Select a {category_type.value} category belonging to your account.",
        )
    return normalized_name


def _get_user_card(card_id: Optional[int], current_user: User, db: Session) -> Optional[Card]:
    if card_id is None:
        return None

    card = db.query(Card).filter(
        Card.id == card_id,
        *_user_scope(Card, current_user),
    ).first()
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found.")
    return card


def _card_balance(db: Session, card: Card, current_user: User) -> Decimal:
    transaction_balance = db.query(
        func.coalesce(
            func.sum(
                case(
                    (IncomeExpense.type == ExpenseType.income, IncomeExpense.amount),
                    (IncomeExpense.type == ExpenseType.expense, -IncomeExpense.amount),
                    else_=0,
                )
            ),
            0,
        )
    ).filter(
        IncomeExpense.account_id == card.id,
        *_user_scope(IncomeExpense, current_user),
    ).scalar()
    return card.balance + transaction_balance


def _notify(
    db: Session,
    current_user: User,
    notification_type: NotificationType,
    **context,
) -> None:
    publish(
        db,
        user_id=current_user.id,
        business_id=getattr(current_user, "current_business_id", None),
        notification_type=notification_type,
        context=context,
    )


def _notify_if_low_balance(
    db: Session,
    current_user: User,
    card: Card,
    balance_before: Decimal,
    balance_after: Decimal,
) -> None:
    # Only on crossing the threshold, so a card that stays low doesn't alert on every expense.
    threshold = notification_settings.low_balance_threshold
    if balance_after <= threshold < balance_before:
        _notify(
            db,
            current_user,
            NotificationType.low_balance,
            card_id=card.id,
            last_4=card.card_number_last_4,
            balance=balance_after,
        )


def _income_expense_response(entry: IncomeExpense) -> IncomeExpenseSummaryResponse:
    return IncomeExpenseSummaryResponse(
        id=entry.id,
        expense_type=entry.type,
        subject=entry.subject,
        category=entry.category,
        amount=entry.amount,
        entry_date=entry.entry_date,
        account_id=entry.account_id,
        description=entry.description,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.get("/transactions", response_model=list[IncomeExpenseSummaryResponse])
async def get_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    entries = db.query(IncomeExpense).filter(
        *_user_scope(IncomeExpense, current_user),
        IncomeExpense.type.in_([ExpenseType.debt, ExpenseType.income, ExpenseType.expense]),
    ).order_by(IncomeExpense.entry_date.desc(), IncomeExpense.id.desc()).all()
    return [_income_expense_response(entry) for entry in entries]


@router.post("/expense", response_model=IncomeExpenseSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(
    subject: str = Form(..., min_length=1, max_length=255),
    category: str = Form(ExpenseCategory.other.value, min_length=1, max_length=80),
    amount: Decimal = Form(..., gt=0, max_digits=12, decimal_places=2),
    entry_date: date = Form(...),
    account_id: int = Form(..., gt=0),
    description: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    card = _get_user_card(account_id, current_user, db)
    category = _require_user_category(db, current_user, category, ExpenseType.expense)
    balance_before = _card_balance(db, card, current_user)
    entry = IncomeExpense(
        user_id=current_user.id,
        business_id=getattr(current_user, "current_business_id", None),
        type=ExpenseType.expense,
        subject=subject,
        category=category,
        amount=amount,
        entry_date=entry_date,
        account_id=account_id,
        description=description,
    )
    db.add(entry)
    try:
        db.flush()
        _notify(
            db,
            current_user,
            NotificationType.expense_created,
            entry_id=entry.id,
            subject=subject,
            amount=amount,
        )
        _notify_if_low_balance(db, current_user, card, balance_before, balance_before - amount)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(entry)
    return _income_expense_response(entry)


@router.post("/income", response_model=IncomeExpenseSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_income(
    subject: str = Form(..., min_length=1, max_length=255),
    category: str = Form(..., min_length=1, max_length=80),
    amount: Decimal = Form(..., gt=0, max_digits=12, decimal_places=2),
    entry_date: date = Form(...),
    account_id: Optional[int] = Form(None, gt=0),
    description: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    _get_user_card(account_id, current_user, db)
    category = _require_user_category(db, current_user, category, ExpenseType.income)
    entry = IncomeExpense(
        user_id=current_user.id,
        business_id=getattr(current_user, "current_business_id", None),
        type=ExpenseType.income,
        subject=subject,
        category=category,
        amount=amount,
        entry_date=entry_date,
        account_id=account_id,
        description=description,

    )
    db.add(entry)
    try:
        db.flush()
        _notify(
            db,
            current_user,
            NotificationType.income_created,
            entry_id=entry.id,
            subject=subject,
            amount=amount,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(entry)
    return _income_expense_response(entry)


@router.post("/cards", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
async def create_card(
    card_number: str = Form(..., min_length=4, max_length=4,regex=r"^\d{4}$"),
    bank_name: Optional[str] = Form(None, max_length=255),
    balance: Decimal = Form(0, ge=0, max_digits=12, decimal_places=2),
    card_color: CardColor = Form(None),
    expiry_month: Optional[int] = Form(None, ge=1, le=12),
    expiry_year: Optional[int] = Form(None, ge=2000, le=2100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    if db.query(Card).filter(
        *_user_scope(Card, current_user),
        Card.card_number_last_4 == card_number,
    ).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A card with this card number already exists.",
        )

    entry = Card(
        user_id=current_user.id,
        business_id=getattr(current_user, "current_business_id", None),
        card_number_last_4=card_number,
        bank_name=bank_name,
        balance=balance,
        card_color=card_color,
        expiry_month=expiry_month,
        expiry_year=expiry_year,
    )
    db.add(entry)
    try:
        db.flush()
        _notify(
            db,
            current_user,
            NotificationType.card_added,
            card_id=entry.id,
            last_4=card_number,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A card with this card number already exists.",
        )
    db.refresh(entry)
    return entry


@router.get("/cards", response_model=list[CardResponse])
async def get_cards(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    return db.query(Card).filter(*_user_scope(Card, current_user)).all()


@router.get("/cards/{card_id}/balance", response_model=CardBalanceResponse)
async def get_card_balance(
    card_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    card = _get_user_card(card_id, current_user, db)
    return CardBalanceResponse(card_id=card_id, balance=_card_balance(db, card, current_user))



@router.post("/cards/{card_id}/balance", response_model=CardBalanceResponse)
async def update_card_balance(
    card_id: int,
    new_balance: Decimal = Form(..., gt=0, max_digits=12, decimal_places=2),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    card = _get_user_card(card_id, current_user, db)
    card.balance = new_balance
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(card)
    return CardBalanceResponse(card_id=card.id, balance=card.balance)


@router.get("/feed-expense-categories", response_model=ExpenseCategoryFeedResponse)
async def feed_expense_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    return {
        "categories": _get_user_categories(db, current_user),
        "colors": list(ExpenseCategoryColor),
    }


@router.post(
    "/categories",
    response_model=ExpenseCategoryOptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_expense_category(
    category_data: ExpenseCategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    name = category_data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Category name cannot be empty.")

    if db.query(ExpenseCategoryOption).filter(
        *_user_scope(ExpenseCategoryOption, current_user),
        ExpenseCategoryOption.name == name,
    ).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A category with this name already exists.",
        )

    _get_user_categories(db, current_user)
    category = ExpenseCategoryOption(
        user_id=current_user.id,
        business_id=getattr(current_user, "current_business_id", None),
        type=category_data.type,
        name=name,
        color=category_data.color.value,
        is_default=False,
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A category with this name already exists.",
        )
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}")
async def delete_expense_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    category = db.query(ExpenseCategoryOption).filter(
        ExpenseCategoryOption.id == category_id,
        *_user_scope(ExpenseCategoryOption, current_user),
    ).first()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")

    db.delete(category)
    db.commit()
    return {"deleted": True, "category_id": category_id}




@router.post("/card/balance/transfer", response_model=CardBalanceResponse)
async def transfer_balance_between_cards(
    source_card_id: int = Form(..., gt=0),
    target_card_id: int = Form(..., gt=0),
    amount: Decimal = Form(..., gt=0, max_digits=12, decimal_places=2),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    if source_card_id == target_card_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Source and target cards must be different.",
        )

    source_card = _get_user_card(source_card_id, current_user, db)
    target_card = _get_user_card(target_card_id, current_user, db)

    if source_card.balance < amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient balance in the source card.",
        )

    balance_before = _card_balance(db, source_card, current_user)
    source_card.balance -= amount
    target_card.balance += amount

    try:
        db.flush()
        _notify(
            db,
            current_user,
            NotificationType.transfer_completed,
            amount=amount,
            source_last_4=source_card.card_number_last_4,
            target_last_4=target_card.card_number_last_4,
        )
        _notify_if_low_balance(
            db, current_user, source_card, balance_before, balance_before - amount
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return CardBalanceResponse(card_id=source_card.id, balance=source_card.balance)



@router.post("/debt", response_model=IncomeExpenseSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_debt(
    subject: str = Form(..., min_length=1, max_length=255),
    category: DebtCategory = Form(...),
    amount: Decimal = Form(..., gt=0, max_digits=12, decimal_places=2),
    entry_date: date = Form(...),
    account_id: Optional[int] = Form(None, gt=0),
    description: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_expense_user),
):
    _get_user_card(account_id, current_user, db)
    entry = IncomeExpense(
        user_id=current_user.id,
        business_id=getattr(current_user, "current_business_id", None),
        type=ExpenseType.debt,
        subject=subject,
        category=category.value,
        amount=amount,
        entry_date=entry_date,
        account_id=account_id,
        description=description,
    )
    db.add(entry)
    try:
        db.flush()
        _notify(
            db,
            current_user,
            NotificationType.debt_created,
            entry_id=entry.id,
            subject=subject,
            amount=amount,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(entry)
    return _income_expense_response(entry)
