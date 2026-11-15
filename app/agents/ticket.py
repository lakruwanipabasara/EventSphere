"""TicketAgent – goal: admit each ticket exactly once."""
import secrets
from datetime import timedelta

from sqlalchemy import select

from app import clock, config
from app.agents.base import Agent
from app.bus import Performative as P
from app.models import Booking, Event, Ticket


class TicketAgent(Agent):
    name = "TicketAgent"
    counters = ["tickets_issued", "admitted", "rejected_scans", "duplicate_scans", "suspicious_tickets"]

    def do_issue_tickets(self, db, msg):
        c = msg.content
        n = int(c.get("quantity", 0))
        booking_id = c.get("booking_id")
        if n < 1 or not booking_id:
            return self.reply(msg, P.FAILURE, reason="Nothing to issue.")
        booking = db.get(Booking, booking_id)
        if booking is None:
            return self.reply(msg, P.FAILURE, reason="Unknown booking.")
        for _ in range(n):                                                                 # T1
            db.add(Ticket(booking_id=booking.id, event_id=booking.event_id,
                          code=secrets.token_urlsafe(16), status="valid"))
        db.flush()
        self.bump(db, "tickets_issued", n)
        return self.reply(msg, P.AGREE, issued=n)

    def do_cancel_tickets(self, db, msg):
        c = msg.content
        tickets = db.scalars(select(Ticket).where(Ticket.booking_id == c.get("booking_id"))).all()
        if not c.get("force") and any(t.status == "used" for t in tickets):
            return self.reply(msg, P.REFUSE, reason="A ticket was already used.")
        for t in tickets:
            t.status = "cancelled"
        db.flush()
        return self.reply(msg, P.AGREE, cancelled=len(tickets))

    def do_check_in(self, db, msg):
        c = msg.content
        now = clock.now()

        def reject(reason, text, **extra):
            self.bump(db, "rejected_scans")
            return self.reply(msg, P.REFUSE, reason=reason, message=text, **extra)

        code = str(c.get("code", "")).strip()
        t = db.scalar(select(Ticket).where(Ticket.code == code)) if code else None
        if t is None:                                                                      # T2
            return reject("unknown_ticket", "Ticket code not recognised.")
        if t.event_id != c.get("event_id"):                                                # T3
            other = db.get(Event, t.event_id)
            return reject("wrong_event", f"This ticket is for a different event: {other.title}.")
        if t.status == "cancelled":                                                        # T4
            return reject("ticket_cancelled", "This ticket was cancelled or refunded.")
        if t.status == "used":                                                             # T5
            t.scan_count += 1
            self.bump(db, "duplicate_scans")
            suspicious = t.scan_count >= config.SUSPICIOUS_SCAN_THRESHOLD
            if t.scan_count == config.SUSPICIOUS_SCAN_THRESHOLD:
                self.bump(db, "suspicious_tickets")
                self.broadcast(db, msg.conversation_id, "suspicious_scan", ticket_id=t.id,
                               event_id=t.event_id, scans=t.scan_count)
            return reject("already_used", f"Already used at {t.used_at:%H:%M}. Scanned {t.scan_count} times.",
                          scans=t.scan_count, suspicious=suspicious)
        ev = db.get(Event, t.event_id)
        if ev.status == "cancelled":
            return reject("event_cancelled", "This event was cancelled.")
        opens = ev.start - timedelta(hours=config.CHECKIN_OPEN_HOURS)                      # T6
        if now < opens or now > ev.end:
            return reject("outside_checkin_time", f"Check-in is open from {opens:%d %b %H:%M} until {ev.end:%H:%M}.")
        t.status, t.used_at, t.scan_count, t.checked_in_by = "used", now, 1, c.get("staff_id")
        db.flush()
        self.bump(db, "admitted")
        self.broadcast(db, msg.conversation_id, "ticket_checked_in", ticket_id=t.id, event_id=ev.id)
        return self.reply(msg, P.AGREE, message="Admit one.", holder=t.booking.user.name,
                          category=t.booking.category.name)
