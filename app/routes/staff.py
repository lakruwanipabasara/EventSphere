from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.agents import bus
from app.db import get_db
from app.models import Event
from app.security import require_role, verify_csrf
from app.web import render

router = APIRouter(prefix="/staff", dependencies=[Depends(verify_csrf)])


class ScanIn(BaseModel):
    code: str
    event_id: int


@router.get("/scanner")
def scanner(request: Request, user=Depends(require_role("staff", "admin")), db: Session = Depends(get_db)):
    since = clock.now() - timedelta(days=1)
    events = db.scalars(select(Event).where(Event.status == "published", Event.end >= since)
                        .order_by(Event.start)).all()
    return render(request, "scanner.html", events=events)


@router.post("/scan")
def scan(body: ScanIn, user=Depends(require_role("staff", "admin")), db: Session = Depends(get_db)):
    rep = bus.request(db, f"staff:{user.id}", "TicketAgent", "check_in",
                      {"code": body.code, "event_id": body.event_id, "staff_id": user.id})
    db.commit()
    ok = rep.performative.value == "AGREE"
    return JSONResponse({"ok": ok, "performative": rep.performative.value,
                         "reason": rep.content.get("reason", "admitted"),
                         "message": rep.content.get("message", ""),
                         "holder": rep.content.get("holder"), "category": rep.content.get("category"),
                         "suspicious": rep.content.get("suspicious", False)})
