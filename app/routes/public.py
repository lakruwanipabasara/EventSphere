"""Attendee-facing pages: browsing, booking, tickets, waitlist, hire, notifications."""
import io
from datetime import datetime
from urllib.parse import urlencode

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import clock
from app.agents import bus
from app.agents.booking import refund_percent
from app.bus import Performative as P
from app.db import get_db
from app.models import Booking, Event, HireRequest, Notification, Ticket, Venue, WaitlistEntry
from app.security import require_login, verify_csrf
from app.web import flash, render, to_float, to_int

router = APIRouter(dependencies=[Depends(verify_csrf)])


def go(url):
    return RedirectResponse(url, status_code=303)


@router.get("/")
def home():
    return go("/events")


# ---------------- browsing ----------------
@router.get("/events")
def events(request: Request, q: str = "", category: str = "", venue_id: str = "", date_from: str = "",
           date_to: str = "", max_price: str = "", db: Session = Depends(get_db)):
    stmt = select(Event).where(Event.status == "published", Event.end >= clock.now()).order_by(Event.start)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Event.title.ilike(like), Event.description.ilike(like)))
    if category:
        stmt = stmt.where(Event.category == category)
    if to_int(venue_id):
        stmt = stmt.where(Event.venue_id == to_int(venue_id))
    try:
        if date_from:
            stmt = stmt.where(Event.start >= datetime.fromisoformat(date_from))
        if date_to:
            stmt = stmt.where(Event.start < datetime.fromisoformat(date_to).replace(hour=23, minute=59))
    except ValueError:
        pass
    items = db.scalars(stmt).all()
    cap = to_float(max_price)
    if cap is not None:
        items = [e for e in items if e.categories and min(c.price for c in e.categories) <= cap]
    return render(request, "events.html", events=items, venues=db.scalars(select(Venue)).all(),
                  f={"q": q, "category": category, "venue_id": venue_id, "date_from": date_from,
                     "date_to": date_to, "max_price": max_price})


@router.get("/events/{event_id}")
def event_detail(event_id: int, request: Request, db: Session = Depends(get_db)):
    ev = db.get(Event, event_id)
    if ev is None or (ev.status == "draft" and not (request.session.get("role") == "admin")):
        raise HTTPException(404, "Event not found.")
    mine, remaining = {}, {}
    uid = request.session.get("uid")
    if uid:
        for en in db.scalars(select(WaitlistEntry).where(
                WaitlistEntry.event_id == ev.id, WaitlistEntry.user_id == uid,
                WaitlistEntry.status.in_(("waiting", "held")))):
            mine[en.category_id] = en
            if en.status == "held":
                remaining[en.id] = max(int((en.hold_expires_at - clock.now()).total_seconds()), 0)
    open_for_sale = ev.status == "published" and clock.now() < ev.start
    return render(request, "event_detail.html", ev=ev, mine=mine, remaining=remaining, open_for_sale=open_for_sale)


# ---------------- booking ----------------
@router.post("/events/{event_id}/book")
def book(event_id: int, request: Request, category_id: int = Form(...), quantity: int = Form(...),
         user=Depends(require_login), db: Session = Depends(get_db)):
    rep = bus.request(db, f"user:{user.id}", "BookingAgent", "book",
                      {"user_id": user.id, "category_id": category_id, "quantity": quantity})
    db.commit()
    if rep.performative == P.AGREE:
        flash(request, f"Payment {rep.content['payment_ref']} recorded (simulated). Your tickets are ready.", "success")
        return go(f"/bookings/{rep.content['booking_id']}")
    flash(request, rep.content.get("reason", "Booking failed."), "danger")
    return go(f"/events/{event_id}")


@router.get("/bookings")
def bookings(request: Request, user=Depends(require_login), db: Session = Depends(get_db)):
    items = db.scalars(select(Booking).where(Booking.user_id == user.id).order_by(Booking.id.desc())).all()
    return render(request, "bookings.html", bookings=items)


@router.get("/bookings/{booking_id}")
def booking_detail(booking_id: int, request: Request, user=Depends(require_login), db: Session = Depends(get_db)):
    b = db.get(Booking, booking_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(404, "Booking not found.")
    hours = (b.event.start - clock.now()).total_seconds() / 3600
    pct = refund_percent(hours) if hours > 0 else 0
    return render(request, "booking_detail.html", b=b, pct=pct, refund_if_cancelled=round(b.total * pct / 100, 2),
                  can_cancel=b.status == "confirmed" and hours > 0)


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: int, request: Request, user=Depends(require_login), db: Session = Depends(get_db)):
    rep = bus.request(db, f"user:{user.id}", "BookingAgent", "cancel_booking",
                      {"user_id": user.id, "booking_id": booking_id})
    db.commit()
    if rep.performative == P.AGREE:
        flash(request, f"Booking cancelled. Refund: Rs {rep.content['refund_amount']:,.2f} "
                       f"({rep.content['refund_percent']}%).", "success")
    else:
        flash(request, rep.content.get("reason", "Could not cancel."), "danger")
    return go(f"/bookings/{booking_id}")


@router.get("/tickets/{ticket_id}/qr.png")
def ticket_qr(ticket_id: int, user=Depends(require_login), db: Session = Depends(get_db)):
    t = db.get(Ticket, ticket_id)
    if t is None or (t.booking.user_id != user.id and user.role not in ("admin", "staff")):
        raise HTTPException(404, "Ticket not found.")
    buf = io.BytesIO()
    qrcode.make(t.code, box_size=8, border=2).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


# ---------------- waitlist ----------------
@router.post("/events/{event_id}/waitlist")
def join_waitlist(event_id: int, request: Request, category_id: int = Form(...), quantity: int = Form(1),
                  user=Depends(require_login), db: Session = Depends(get_db)):
    rep = bus.request(db, f"user:{user.id}", "WaitlistAgent", "join",
                      {"user_id": user.id, "event_id": event_id, "category_id": category_id, "quantity": quantity})
    db.commit()
    if rep.performative == P.AGREE:
        flash(request, f"You are on the waitlist (position {rep.content['position']}).", "success")
    else:
        flash(request, rep.content.get("reason", "Could not join the waitlist."), "danger")
    return go(f"/events/{event_id}")


@router.post("/waitlist/{entry_id}/claim")
def claim(entry_id: int, request: Request, user=Depends(require_login), db: Session = Depends(get_db)):
    rep = bus.request(db, f"user:{user.id}", "WaitlistAgent", "claim", {"user_id": user.id, "entry_id": entry_id})
    db.commit()
    if rep.performative == P.AGREE:
        flash(request, "Seats claimed. Your tickets are ready.", "success")
        return go(f"/bookings/{rep.content['booking_id']}")
    flash(request, rep.content.get("reason", "Could not claim."), "danger")
    en = db.get(WaitlistEntry, entry_id)
    return go(f"/events/{en.event_id}" if en else "/events")


@router.post("/waitlist/{entry_id}/leave")
def leave(entry_id: int, request: Request, user=Depends(require_login), db: Session = Depends(get_db)):
    en = db.get(WaitlistEntry, entry_id)
    rep = bus.request(db, f"user:{user.id}", "WaitlistAgent", "leave", {"user_id": user.id, "entry_id": entry_id})
    db.commit()
    flash(request, "You left the waitlist." if rep.performative == P.AGREE else rep.content.get("reason", "Error"),
          "info" if rep.performative == P.AGREE else "danger")
    return go(f"/events/{en.event_id}" if en else "/events")


# ---------------- private venue hire ----------------
def _hire_page(request, db, user, form, proposal=None, status_code=200):
    mine = db.scalars(select(HireRequest).where(HireRequest.user_id == user.id).order_by(HireRequest.id.desc())).all()
    venues = db.scalars(select(Venue).where(Venue.open_for_hire.is_(True))).all()
    return render(request, "hire.html", status_code=status_code, venues=venues, mine=mine, form=form, proposal=proposal)


@router.get("/hire")
def hire_form(request: Request, venue_id: str = "", start: str = "", end: str = "", guests: str = "",
              purpose: str = "", user=Depends(require_login), db: Session = Depends(get_db)):
    return _hire_page(request, db, user, {"venue_id": venue_id, "start": start, "end": end,
                                          "guests": guests, "purpose": purpose})


@router.post("/hire")
def hire_submit(request: Request, venue_id: int = Form(...), start: str = Form(""), end: str = Form(""),
                guests: int = Form(0), purpose: str = Form(""), user=Depends(require_login),
                db: Session = Depends(get_db)):
    form = {"venue_id": str(venue_id), "start": start, "end": end, "guests": str(guests), "purpose": purpose}
    rep = bus.request(db, f"user:{user.id}", "VenueAgent", "hire_request",
                      {"user_id": user.id, "venue_id": venue_id, "start": start, "end": end,
                       "guests": guests, "purpose": purpose})
    db.commit()
    c = rep.content
    if rep.performative == P.AGREE:
        flash(request, f"Confirmed instantly. Request #{c['request_id']} for {c['venue_name']}: "
                       f"Rs {c['price']:,.2f}.", "success")
        return go("/hire")
    if rep.performative == P.INFORM:
        flash(request, f"Request #{c['request_id']} quoted at Rs {c['price']:,.2f} and sent to the admin: "
                       f"{c['reason']}.", "info")
        return go("/hire")
    if rep.performative == P.PROPOSE:
        base = {"guests": guests, "purpose": purpose}
        proposal = {"reason": c["reason"],
                    "times": [dict(t, url="/hire?" + urlencode({**base, "venue_id": venue_id, **t}))
                              for t in c.get("alternative_times", [])],
                    "venues": [dict(v, url="/hire?" + urlencode({**base, "venue_id": v["venue_id"],
                                                                 "start": v["start"], "end": v["end"]}))
                               for v in c.get("alternative_venues", [])]}
        return _hire_page(request, db, user, form, proposal=proposal)
    flash(request, c.get("reason", "Request could not be processed."), "danger")
    return _hire_page(request, db, user, form, status_code=200)


# ---------------- notifications ----------------
@router.get("/notifications")
def notifications(request: Request, user=Depends(require_login), db: Session = Depends(get_db)):
    cond = Notification.user_id == user.id
    if user.role == "admin":
        cond = or_(cond, Notification.role == "admin")
    items = db.scalars(select(Notification).where(cond).order_by(Notification.id.desc()).limit(100)).all()
    return render(request, "notifications.html", items=items)
