import secrets
from types import SimpleNamespace

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User


class NeedLogin(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request):
    """Reject unsafe requests without a valid CSRF token (form field or X-CSRF-Token header)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("x-csrf-token")
    if not sent:
        form = await request.form()
        sent = form.get("csrf_token")
    expected = request.session.get("csrf")
    if not expected or not sent or not secrets.compare_digest(str(sent).encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token. Reload the page and try again.")


def session_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    return SimpleNamespace(id=uid, name=request.session.get("name", ""), role=request.session.get("role", "attendee"))


def current_user(request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("uid")
    return db.get(User, uid) if uid else None


def require_login(user=Depends(current_user)):
    if user is None:
        raise NeedLogin()
    return user


def require_role(*roles):
    def checker(user=Depends(require_login)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission to open this page.")
        return user
    return checker
