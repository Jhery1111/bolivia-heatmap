"""
auth.py
-------
JWT-based authentication for the SIGMAC API.

Demo credentials (development only):
    username: admin
    password: sigmac2024

In production, replace the hard-coded user store with a real DB lookup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from .config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class TokenData(BaseModel):
    username: str
    scopes: list[str] = []


class User(BaseModel):
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    disabled: bool = False
    scopes: list[str] = []


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Demo user store — replace with DB lookup in production
# ---------------------------------------------------------------------------

# Pre-hashed bcrypt hash of "sigmac2024"
_DEMO_USERS: dict[str, dict] = {
    "admin": {
        "username": "admin",
        "full_name": "Administrador SIGMAC",
        "email": "admin@dntt.gob.bo",
        "hashed_password": _pwd_context.hash("sigmac2024"),
        "disabled": False,
        "scopes": ["read", "write", "admin"],
    },
    "viewer": {
        "username": "viewer",
        "full_name": "Observador SIGMAC",
        "email": "viewer@dntt.gob.bo",
        "hashed_password": _pwd_context.hash("viewer2024"),
        "disabled": False,
        "scopes": ["read"],
    },
}


def _get_user(username: str) -> Optional[dict]:
    return _DEMO_USERS.get(username)


def _verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def _authenticate_user(username: str, password: str) -> Optional[dict]:
    user = _get_user(username)
    if not user:
        return None
    if not _verify_password(password, user["hashed_password"]):
        return None
    if user.get("disabled"):
        return None
    return user


# ---------------------------------------------------------------------------
# Token creation / verification
# ---------------------------------------------------------------------------

def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT.

    Parameters
    ----------
    data:
        Payload dict.  A ``sub`` key is expected.
    expires_delta:
        Token lifetime.  Defaults to ``ACCESS_TOKEN_EXPIRE_MINUTES``.

    Returns
    -------
    str
        Encoded JWT string.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_token(token: str) -> TokenData:
    """Decode and validate a JWT, returning its parsed payload.

    Raises
    ------
    HTTPException 401
        If the token is invalid, expired, or malformed.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
        scopes: list[str] = payload.get("scopes", [])
        return TokenData(username=username, scopes=scopes)
    except JWTError as exc:
        logger.warning("JWT verification failed", error=str(exc))
        raise credentials_exception from exc


# ---------------------------------------------------------------------------
# FastAPI OAuth2 scheme + dependency
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    """FastAPI dependency that resolves the authenticated User from the JWT.

    Raises HTTP 401 for invalid tokens, HTTP 403 for disabled accounts.
    """
    token_data = verify_token(token)
    user_dict = _get_user(token_data.username)
    if user_dict is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = User(**{k: v for k, v in user_dict.items() if k != "hashed_password"})
    if user.disabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta deshabilitada",
        )
    return user


# Convenient typed alias for injection.
CurrentUser = Annotated[User, Depends(get_current_user)]

# ---------------------------------------------------------------------------
# Auth router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/token",
    response_model=Token,
    summary="Obtener token JWT",
    description="Autentica con usuario/contraseña y devuelve un JWT Bearer.",
)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    user = _authenticate_user(form_data.username, form_data.password)
    if not user:
        logger.warning("Failed login attempt", username=form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expire_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"], "scopes": user["scopes"]},
        expires_delta=expire_delta,
    )
    logger.info("Successful login", username=user["username"])
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=int(expire_delta.total_seconds()),
    )


@router.get(
    "/me",
    response_model=User,
    summary="Perfil del usuario autenticado",
)
async def read_users_me(current_user: CurrentUser) -> User:
    return current_user
