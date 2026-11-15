"""Admin / organiser pages: dashboard, venues, events, hire approvals, message log."""
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.agents import bus
from app.bus import Message, Performative as P, new_conversation_id
from app.db import get_db
from app.models import Event, HireRequest, TicketCategory, User, Venue
from app.security import hash_password, require_role, verify_csrf
from app.stats import agent_beliefs, checkins_by_hour, event_stats, recent_messages
from app.web import flash, render, to_float, to_int

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_csrf), Depends(require_role("admin"))])


def go(url):
    return RedirectResponse(url, status_code=303)


# ---------------- dashboard ----------------
@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    stats = event_stats(db)
    hours, counts = checkins_by_hour(db)
    return render(request, "admin_dashboard.html", stats=stats, messages=recent_messages(db, limit=15),
                  beliefs=agent_beliefs(db), chart={
                      "labels": [s["event"].title[:22] for s in stats],
                      "sold": [s["sold"] for s in stats], "checkins": [s["checkins"] for s in stats],
                      "hours": hours, "hour_counts": counts})


@router.get("/messages")
def messages(request: Request, conv: str = "", db: Session = Depends(get_db)):
    return render(request, "admin_messages.html", messages=recent_messages(db, conv=conv.strip() or None, limit=300),
                  conv=conv.strip())


# ---------------- venues ----------------
def _venue_from_form(v, name, location, capacity, description, image_url, hourly_rate, open_for_hire):
    v.name, v.location, v.capacity = name.strip(), location.strip(), capacity
    v.description, v.image_url, v.hourly_rate = description.strip(), image_url.strip(), hourly_rate
    v.open_for_hire = bool(open_for_hire)


@router.get("/venues")
def venues(request: Request, db: Session = Depends(get_db)):
    return render(request, "admin_venues.html", venues=db.scalars(select(Venue).order_by(Venue.name)).all())


@router.get("/venues/new")
def venue_new(request: Request):
    return render(request, "admin_venue_form.html", v=None)


@router.get("/venues/{venue_id}/edit")
def venue_edit(venue_id: int, request: Request, db: Session = Depends(get_db)):
    v = db.get(Venue, venue_id)
    if v is None:
        raise HTTPException(404, "Venue not found.")
    return render(request, "admin_venue_form.html", v=v)


@router.post("/venues/save")
def venue_save(request: Request, venue_id: str = Form(""), name: str = Form(""), location: str = Form(""),
               capacity: int = Form(0), description: str = Form(""), image_url: str = Form(""),
               hourly_rate: float = Form(0), open_for_hire: str = Form(""), db: Session = Depends(get_db)):
    if not name.strip() or capacity < 1 or hourly_rate < 0:
        flash(request, "Venue needs a name, a capacity of at least 1 and a non-negative hourly rate.", "danger")
        return go("/admin/venues/new" if not venue_id else f"/admin/venues/{venue_id}/edit")
    v = db.get(Venue, to_int(venue_id)) if venue_id else Venue()
    if v is None:
        raise HTTPException(404, "Venue not found.")
    _venue_from_form(v, name, location, capacity, description, image_url, hourly_rate, open_for_hire)
    db.add(v)
    db.commit()
    flash(request, f"Venue '{v.name}' saved.", "success")
    return go("/admin/venues")


# ---------------- events ----------------
@router.get("/events")
def events(request: Request, db: Session = Depends(get_db)):
    return render(request, "admin_events.html", events=db.scalars(select(Event).order_by(Event.start.desc())).all(),
                  now=clock.now())


@router.get("/events/new")
def event_new(request: Request, db: Session = Depends(get_db)):
    return render(request, "admin_event_form.html", ev=None, form=None, venues=db.scalars(select(Venue)).all())


@router.get("/events/{event_id}/edit")
def event_edit(event_id: int, request: Request, db: Session = Depends(get_db)):
    ev = db.get(Event, event_id)
    if ev is None or ev.status == "cancelled":
        raise HTTPException(404, "Event not found or cancelled.")
    return render(request, "admin_event_form.html", ev=ev, form=None, venues=db.scalars(select(Venue)).all())


@router.post("/events/save")
def event_save(request: Request, user=Depends(require_role("admin")), event_id: str = Form(""),
               title: str = Form(""), category: str = Form("Other"), venue_id: int = Form(0),
               start: str = Form(""), end: str = Form(""), description: str = Form(""),
               action: str = Form("draft"), cat_id: list[str] = Form([]), cat_name: list[str] = Form([]),
               cat_price: list[str] = Form([]), cat_qty: list[str] = Form([]), db: Session = Depends(get_db)):
    ev = db.get(Event, to_int(event_id)) if event_id else None
    if event_id and (ev is None or ev.status == "cancelled"):
        raise HTTPException(404, "Event not found or cancelled.")
    form = {"title": title, "category": category, "venue_id": venue_id, "start": start, "end": end,
            "description": description, "rows": list(zip(cat_id + [""] * 9, cat_name, cat_price, cat_qty))}

    def fail(msg, suggestions=None):
        db.commit()   # keep the message log of the VenueAgent conversation
        return render(request, "admin_event_form.html", status_code=400, ev=ev, form=form, error=msg,
                      suggestions=suggestions, venues=db.scalars(select(Venue)).all())

    try:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        return fail("Enter valid start and end times.")
    if not title.strip():
        return fail("Event title is required.")
    rows = []
    for rid, name, price, qty in zip(cat_id + [""] * len(cat_name), cat_name, cat_price, cat_qty):
        if not name.strip() and not rid:
            continue
        p, q = to_float(price), to_int(qty)
        if name.strip() and (p is None or p < 0 or q is None or q < 1):
            return fail(f"Ticket category '{name}' needs a price (0 or more) and a quantity of at least 1.")
        rows.append((to_int(rid), name.strip(), p, q))
    live = [r for r in rows if r[1]]
    if not live:
        return fail("Add at least one ticket category (name, price, quantity).")
    existing = {c.id: c for c in ev.categories} if ev else {}
    for rid, name, p, q in live:
        c = existing.get(rid)
        if c and q < c.sold + c.held:
            return fail(f"'{name}': quantity cannot be below tickets already sold or held ({c.sold + c.held}).")

    conv = new_conversation_id()
    rep = bus.request(db, f"user:{user.id}", "VenueAgent", "check_slot",
                      {"venue_id": venue_id, "start": s.isoformat(), "end": e.isoformat(),
                       "total_tickets": sum(r[3] for r in live), "exclude_event_id": ev.id if ev else None}, conv)
    if rep.performative != P.AGREE:
        return fail(rep.content.get("reason", "Venue refused the slot."), rep.content.get("suggested_slots"))

    if ev is None:
        ev = Event()
        db.add(ev)
    ev.title, ev.category, ev.venue_id = title.strip(), category, venue_id
    ev.start, ev.end, ev.description = s, e, description.strip()
    if action == "publish":
        ev.status = "published"
    elif ev.status != "published":
        ev.status = "draft"
    db.flush()
    keep = set()
    for rid, name, p, q in live:
        c = existing.get(rid)
        if c is None:
            c = TicketCategory(event_id=ev.id, sold=0, held=0)
            db.add(c)
        c.name, c.price, c.quantity = name, p, q
        db.flush()
        keep.add(c.id)
    for rid, c in existing.items():           # a blanked-out category with no sales is removed
        if rid not in keep and c.sold == 0 and c.held == 0:
            db.delete(c)
    db.commit()
    flash(request, f"Event '{ev.title}' {'published' if ev.status == 'published' else 'saved as draft'}.", "success")
    return go("/admin/events")


@router.post("/events/{event_id}/cancel")
def event_cancel(event_id: int, request: Request, user=Depends(require_role("admin")), db: Session = Depends(get_db)):
    ev = db.get(Event, event_id)
    if ev is None or ev.status != "published":
        flash(request, "Only published events can be cancelled.", "danger")
        return go("/admin/events")
    ev.status = "cancelled"
    db.flush()
    bus.send(db, Message(sender=f"user:{user.id}", receiver="*", performative=P.INFORM,
                         content={"event": "event_cancelled", "event_id": ev.id, "event_title": ev.title}))
    db.commit()
    flash(request, f"'{ev.title}' cancelled. All bookings were refunded in full.", "success")
    return go("/admin/events")


# ---------------- private hire approvals ----------------
@router.get("/hire")
def hire_list(request: Request, db: Session = Depends(get_db)):
    reqs = db.scalars(select(HireRequest).order_by(HireRequest.status.desc(), HireRequest.id.desc())).all()
    return render(request, "admin_hire.html", requests=reqs)


@router.post("/hire/{request_id}/decide")
def hire_decide(request_id: int, request: Request, decision: str = Form(...), note: str = Form(""),
                user=Depends(require_role("admin")), db: Session = Depends(get_db)):
    rep = bus.request(db, f"user:{user.id}", "VenueAgent", "hire_decision",
                      {"request_id": request_id, "approve": decision == "approve", "note": note})
    db.commit()
    if rep.performative == P.AGREE:
        flash(request, f"Request #{request_id} {rep.content['status']}.", "success")
    else:
        flash(request, rep.content.get("reason", "Could not decide."), "danger")
    return go("/admin/hire")


# ---------------- user accounts (admin creates staff / admin accounts) ----------------
@router.get("/users")
def users(request: Request, db: Session = Depends(get_db)):
    return render(request, "admin_users.html", users=db.scalars(select(User).order_by(User.role, User.name)).all(), form={})


@router.post("/users/create")
def user_create(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form(""),
                role: str = Form("staff"), db: Session = Depends(get_db)):
    name, email = name.strip(), email.strip().lower()
    error = None
    if not name or "@" not in email:
        error = "Enter a name and a valid email address."
    elif role not in ("admin", "staff", "attendee"):
        error = "Choose a valid role."
    elif not 8 <= len(password) <= 64:
        error = "Password must be 8 to 64 characters."
    elif db.scalar(select(User.id).where(User.email == email)):
        error = "An account with this email already exists."
    if error:
        return render(request, "admin_users.html", status_code=400, error=error,
                      users=db.scalars(select(User).order_by(User.role, User.name)).all(),
                      form={"name": name, "email": email, "role": role})
    db.add(User(name=name, email=email, password_hash=hash_password(password), role=role))
    db.commit()
    flash(request, f"{role.title()} account created for {email}.", "success")
    return go("/admin/users")
