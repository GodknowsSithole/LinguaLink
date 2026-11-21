"""Password hashing and JWT access tokens."""

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

# The signing secret comes from the environment; the fallback is for local development only.
SECRET = os.getenv("JWT_SECRET", "change-this-development-secret")
TOKEN_LIFETIME = timedelta(days=7)

password_hasher = PasswordHash.recommended()  # Argon2
bearer = HTTPBearer()


def hash_password(password):
    return password_hasher.hash(password)


def verify_password(password, password_hash):
    return password_hasher.verify(password, password_hash)


def token(user_id):
    """Create a signed token that identifies the user and expires after seven days."""
    claims = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + TOKEN_LIFETIME,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


def decode(access_token):
    """Return the user id inside a token, or raise 401 if it is invalid or expired."""
    try:
        return int(jwt.decode(access_token, SECRET, algorithms=["HS256"])["sub"])
    except Exception:
        raise HTTPException(401, "Invalid or expired token")


def current_user_id(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    """FastAPI dependency: the id of the user who sent the Bearer token."""
    return decode(credentials.credentials)
