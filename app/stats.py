"""Organiser dashboard numbers."""
from collections import Counter

from sqlalchemy import func, select

from app.models import AgentState, Booking, Event, MessageLog, Ticket


def event_stats(db):
    rows = []
    for ev in db.scalars(select(Event).order_by(Event.start.desc())):
        sold = sum(c.sold for c in ev.categories)
        planned = sum(c.quantity for c in ev.categories)
        revenue = db.scalar(select(func.coalesce(func.sum(Booking.total - Booking.refund_amount), 0))
                            .where(Booking.event_id == ev.id)) or 0
        checkins = db.scalar(select(func.count(Ticket.id)).where(Ticket.event_id == ev.id, Ticket.status == "used")) or 0
        rows.append({"event": ev, "sold": sold, "planned": planned, "capacity": ev.venue.capacity,
                     "revenue": revenue, "checkins": checkins,
                     "fill": round(100 * sold / ev.venue.capacity) if ev.venue.capacity else 0})
    return rows


def checkins_by_hour(db):
    used = db.scalars(select(Ticket.used_at).where(Ticket.status == "used", Ticket.used_at.is_not(None))).all()
    counts = Counter(u.strftime("%d %b %H:00") for u in sorted(used))
    return list(counts.keys()), list(counts.values())


def agent_beliefs(db):
    out = {}
    for row in db.scalars(select(AgentState).order_by(AgentState.agent, AgentState.key)):
        out.setdefault(row.agent, []).append((row.key, row.value))
    return out


def recent_messages(db, conv=None, limit=200):
    q = select(MessageLog).order_by(MessageLog.id.desc()).limit(limit)
    if conv:
        q = select(MessageLog).where(MessageLog.conversation_id == conv).order_by(MessageLog.id.asc())
    return db.scalars(q).all()
