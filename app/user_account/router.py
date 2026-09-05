from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import User
from ..schema import UserCreate, UserOut, Token
from ..db import get_db
from ..auth import authenticate_account, hash_password, verify_password, create_access_token
from .dependencies import get_current_user
from .schemas import ChangePasswordRequest, LoginRequest, UserProfileUpdate

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    conflicts = []
    if db.query(User).filter(User.username == user_in.username).first():
        conflicts.append("username")
    if db.query(User).filter(User.email == user_in.email).first():
        conflicts.append("email")
    if db.query(User).filter(User.phone == user_in.phone).first():
        conflicts.append("phone")

    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The following field(s) are already registered: {', '.join(conflicts)}.",
        )

    user = User(
        username=user_in.username,
        email=user_in.email,
        phone=user_in.phone,
        password_hash=hash_password(user_in.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with these details already exists.",
        )
    db.refresh(user)

    return user

@router.post('/login', response_model=Token)
def login(user_in: LoginRequest, db: Session = Depends(get_db)):
    access_token, slug, redirect_to = authenticate_account(user_in.email_or_phone, user_in.password, db)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "slug": slug,
        "redirect_to": redirect_to,
    }


@router.get("/me", response_model=UserOut)
def get_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
def update_profile(
    profile: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    changes = profile.model_dump(exclude_unset=True)
    for field, value in changes.items():
        conflict = db.query(User).filter(
            getattr(User, field) == value,
            User.id != current_user.id,
        ).first()
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"The {field} is already in use.",
            )
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(request.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if request.current_password == request.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password.",
        )

    current_user.password_hash = hash_password(request.new_password)
    db.commit()


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db.delete(current_user)
    db.commit()


