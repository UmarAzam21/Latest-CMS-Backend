from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import (
    create_access_token,
    get_current_admin,
    hash_password,
    security,
    verify_password,
)
from ..db import get_db
from ..models import AgentLogin
from ..schema import (
    AgentsLoginRequest,
    AgentsLoginResponse,
    AgentsRegisterRequest,
    AgentsRegisterResponse,
)

router = APIRouter(prefix="/agents", tags=["Agents"])


@router.post("/login", response_model=AgentsLoginResponse, status_code=status.HTTP_200_OK)
def login_agent(request: AgentsLoginRequest, db: Session = Depends(get_db)):
    agent = db.query(AgentLogin).filter(AgentLogin.email == request.email).first()
    if agent is None or not verify_password(request.password, agent.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not agent.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This agent account is inactive.",
        )

    agent.last_login_at = datetime.now()
    db.commit()
    token = create_access_token(
        data={"sub": agent.email, "agent_id": agent.id, "role": agent.role}
    )
    is_admin = agent.role == "admin"
    profile = {
        "id": agent.id,
        "username": agent.username,
        "email": agent.email,
        "role": agent.role,
        "profile_type": "admin" if is_admin else "user",
    }
    if is_admin:
        profile["permissions"] = ["*"]

    return {
        "message": "Login successful",
        "token": token,
        "token_type": "bearer",
        "slug": "admin" if is_admin else "user",
        "redirect_to": "/admin-dashboard" if is_admin else "/user-dashboard",
        "profile": profile,
    }


@router.post("/register", response_model=AgentsRegisterResponse, status_code=status.HTTP_201_CREATED)
def register_agent(
    request: AgentsRegisterRequest,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    if request.role == "admin":
        get_current_admin(credentials=credentials, db=db)

    if db.query(AgentLogin).filter(
        (AgentLogin.email == request.email)
        | (AgentLogin.username == request.username)
    ).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An agent with this email or username already exists.",
        )

    agent = AgentLogin(
        username=request.username,
        email=request.email,
        password_hash=hash_password(request.password),
        role=request.role,
    )
    db.add(agent)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An agent with this email or username already exists.",
        )
    db.refresh(agent)
    return {
        "message": "Registration successful",
        "agent_id": agent.id,
        "username": agent.username,
        "role": agent.role,
    }