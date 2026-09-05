from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email_or_phone: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserProfileUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, min_length=1, max_length=20)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
