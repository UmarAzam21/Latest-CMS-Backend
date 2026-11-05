from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError

from ...auth import decode_access_token
from ...db import SessionLocal
from ...user_account.dependencies import get_current_user
from ..auth.dependencies import get_current_expense_user


@dataclass(frozen=True)
class SocketIdentity:
    user_id: int
    business_id: Optional[int]
    expires_at: int


def authenticate_socket_token(token: str) -> Optional[SocketIdentity]:
    """Apply the same checks as the REST API to a token sent over a WebSocket."""
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with SessionLocal() as db:
        try:
            user = get_current_expense_user(
                credentials=credentials,
                current_user=get_current_user(credentials=credentials, db=db),
                db=db,
            )
            return SocketIdentity(
                user_id=user.id,
                business_id=user.current_business_id,
                expires_at=decode_access_token(token)["exp"],
            )
        except (HTTPException, JWTError, KeyError):
            return None
