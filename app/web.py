"""Shared web helpers: templates, flash messages, rendering."""
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.security import csrf_token, session_user

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["money"] = lambda v: f"Rs {float(v or 0):,.2f}"
templates.env.filters["dt"] = lambda d: d.strftime("%a %d %b %Y, %H:%M") if d else ""
templates.env.filters["dtinput"] = lambda d: d.strftime("%Y-%m-%dT%H:%M") if d else ""

EVENT_CATEGORIES = ["Concert", "Workshop", "Seminar", "Conference", "Other"]


def flash(request: Request, message: str, kind: str = "info"):
    request.session["flash"] = request.session.get("flash", []) + [[kind, message]]


def render(request: Request, name: str, status_code: int = 200, **ctx):
    ctx.setdefault("user", session_user(request))
    ctx["csrf_token"] = csrf_token(request)
    ctx["flashes"] = request.session.pop("flash", [])
    ctx["event_categories"] = EVENT_CATEGORIES
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def safe_next(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/events"


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
