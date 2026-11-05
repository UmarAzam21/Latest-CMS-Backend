import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...auth import create_access_token, hash_password, verify_password
from ...models import ExpenseManagerAuth, ExpenseManagerBusiness, User
from ...schema import ExpenseManagerBusinessCreateRequest, ExpenseManagerRegisterRequest
from ..notification.events import NotificationType
from ..notification.service import publish

MAX_FAILED_PIN_ATTEMPTS = 5
PIN_LOCKOUT_MINUTES = 15
TOKEN_SCOPE = "expense_manager"

# Compared against when the phone number is unknown, so a miss costs the same
# bcrypt time as a hit and response timing doesn't reveal registered numbers.
_DUMMY_PIN_HASH = hash_password(secrets.token_hex(8))

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid phone number or PIN.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _now() -> datetime:
    # Columns are timezone-naive; store and compare everything as naive UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _raise_if_locked(account: ExpenseManagerAuth) -> None:
    if account.locked_until and account.locked_until > _now():
        retry_after = int((account.locked_until - _now()).total_seconds()) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many incorrect PIN attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def _record_failed_attempt(db: Session, account: ExpenseManagerAuth) -> None:
    account.failed_attempts = (account.failed_attempts or 0) + 1
    if account.failed_attempts >= MAX_FAILED_PIN_ATTEMPTS:
        account.locked_until = _now() + timedelta(minutes=PIN_LOCKOUT_MINUTES)
        account.failed_attempts = 0
        publish(
            db,
            user_id=account.user_id,
            notification_type=NotificationType.account_locked,
            context={"minutes": PIN_LOCKOUT_MINUTES},
        )
    db.commit()


def _verify_pin(db: Session, account: ExpenseManagerAuth, pin: str) -> bool:
    """Check a PIN against the account, enforcing the lockout either way."""
    _raise_if_locked(account)
    if verify_password(pin, account.pin_hash):
        account.failed_attempts = 0
        account.locked_until = None
        return True
    _record_failed_attempt(db, account)
    return False


def create_token_for(account: ExpenseManagerAuth, business_id: int) -> str:
    # `sub` is the linked User id so the existing get_current_user dependency
    # (and every /expenses endpoint) works with this token unchanged.
    return create_access_token(
        data={
            "sub": str(account.user_id),
            "slug": "user",
            "scope": TOKEN_SCOPE,
            "business_id": business_id,
        }
    )


def register_account(db: Session, data: ExpenseManagerRegisterRequest) -> ExpenseManagerAuth:
    conflicts = []
    if db.query(ExpenseManagerAuth).filter(ExpenseManagerAuth.phone_number == data.phone_number).first() \
            or db.query(User).filter(or_(User.phone == data.phone_number, User.username == data.phone_number)).first():
        conflicts.append("phone_number")
    if data.email and (
        db.query(ExpenseManagerAuth).filter(ExpenseManagerAuth.email == data.email).first()
        or db.query(User).filter(User.email == data.email).first()
    ):
        conflicts.append("email")
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The following field(s) are already registered: {', '.join(conflicts)}.",
        )

    # The backing User gets a random, never-shared password: this account signs
    # in with phone + PIN only, so the PIN lockout can't be bypassed via /auth/login.
    user = User(
        username=data.phone_number,
        email=data.email,
        phone=data.phone_number,
        password_hash=hash_password(secrets.token_urlsafe(32)),
    )
    db.add(user)
    try:
        db.flush()
        account = ExpenseManagerAuth(
            user_id=user.id,
            phone_number=data.phone_number,
            business_name=data.business_name,
            email=data.email,
            pin_hash=hash_password(data.pin),
        )
        db.add(account)
        db.flush()
        db.add(
            ExpenseManagerBusiness(
                account_id=account.id,
                name=data.business_name,
                business_type=data.business_type,
                currency=data.currency,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with these details already exists.",
        )
    db.refresh(account)
    return account


def ensure_initial_business(db: Session, account: ExpenseManagerAuth) -> ExpenseManagerBusiness:
    business = db.query(ExpenseManagerBusiness).filter(
        ExpenseManagerBusiness.account_id == account.id
    ).order_by(ExpenseManagerBusiness.id).first()
    if business is None:
        business = ExpenseManagerBusiness(
            account_id=account.id,
            name=account.business_name,
            business_type="personal",
            currency="PKR",
        )
        db.add(business)
        db.commit()
        db.refresh(business)
    return business


def list_businesses(db: Session, account: ExpenseManagerAuth) -> list[ExpenseManagerBusiness]:
    ensure_initial_business(db, account)
    return db.query(ExpenseManagerBusiness).filter(
        ExpenseManagerBusiness.account_id == account.id
    ).order_by(ExpenseManagerBusiness.id).all()


def create_business(
    db: Session,
    account: ExpenseManagerAuth,
    data: ExpenseManagerBusinessCreateRequest,
) -> ExpenseManagerBusiness:
    business = ExpenseManagerBusiness(
        account_id=account.id,
        name=data.business_name,
        business_type=data.business_type,
        currency=data.currency,
    )
    db.add(business)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A business with this name already exists in your account.",
        )
    db.refresh(business)
    return business


def get_business(
    db: Session,
    account: ExpenseManagerAuth,
    business_id: int,
) -> ExpenseManagerBusiness:
    business = db.query(ExpenseManagerBusiness).filter(
        ExpenseManagerBusiness.id == business_id,
        ExpenseManagerBusiness.account_id == account.id,
    ).first()
    if business is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found.",
        )
    return business


def authenticate(db: Session, phone_number: str, pin: str) -> ExpenseManagerAuth:
    account = db.query(ExpenseManagerAuth).filter(
        ExpenseManagerAuth.phone_number == phone_number
    ).first()
    if account is None:
        verify_password(pin, _DUMMY_PIN_HASH)
        raise _INVALID_CREDENTIALS

    if not _verify_pin(db, account, pin):
        raise _INVALID_CREDENTIALS

    if not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This expense manager account is inactive.",
        )

    account.last_login_at = _now()
    db.commit()
    db.refresh(account)
    return account


def change_pin(db: Session, account: ExpenseManagerAuth, current_pin: str, new_pin: str) -> None:
    if not _verify_pin(db, account, current_pin):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current PIN is incorrect.",
        )
    if current_pin == new_pin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New PIN must be different from the current PIN.",
        )

    account.pin_hash = hash_password(new_pin)
    publish(db, user_id=account.user_id, notification_type=NotificationType.pin_changed)
    db.commit()
