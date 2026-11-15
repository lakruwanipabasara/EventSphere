from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import hash_password, verify_csrf, verify_password
from app.web import flash, render, safe_next

router = APIRouter(dependencies=[Depends(verify_csrf)])


def _login_session(request, user):
    request.session.clear()
    request.session.update({"uid": user.id, "name": user.name, "role": user.role})


@router.get("/register")
def register_form(request: Request):
    return render(request, "register.html", form={})


@router.post("/register")
def register(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""),
             db: Session = Depends(get_db)):
    name, email = name.strip(), email.strip().lower()
    error = None
    if not name or "@" not in email:
        error = "Enter your name and a valid email address."
    elif not 8 <= len(password) <= 64:
        error = "Password must be 8 to 64 characters."
    elif db.scalar(select(User.id).where(User.email == email)):
        error = "An account with this email already exists."
    if error:
        return render(request, "register.html", status_code=400, error=error, form={"name": name, "email": email})
    user = User(name=name, email=email, password_hash=hash_password(password), role="attendee")
    db.add(user)
    db.commit()
    _login_session(request, user)
    flash(request, f"Welcome, {user.name}!", "success")
    return RedirectResponse("/events", status_code=303)


@router.get("/login")
def login_form(request: Request, next: str = "/events"):
    return render(request, "login.html", next=safe_next(next), form={})


@router.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form(""), next: str = Form("/events"),
          db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not verify_password(password, user.password_hash):
        return render(request, "login.html", status_code=400, error="Wrong email or password.",
                      next=safe_next(next), form={"email": email})
    _login_session(request, user)
    flash(request, f"Signed in as {user.name}.", "success")
    target = safe_next(next)
    if target == "/events" and user.role == "admin":
        target = "/admin"
    elif target == "/events" and user.role == "staff":
        target = "/staff/scanner"
    return RedirectResponse(target, status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
