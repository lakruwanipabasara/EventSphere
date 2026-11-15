"""BookingAgent – goal: sell tickets fairly without overselling."""
import secrets

from sqlalchemy import func, select, update

from app import clock, config
from app.agents.base import Agent
from app.bus import Performative as P
from app.models import Booking, Ticket, TicketCategory


def refund_percent(hours_to_start: float) -> int:
    """Rule B7: refund policy for attendee cancellations."""
    if hours_to_start >= config.REFUND_FULL_HOURS:
        return 100
    if hours_to_start >= config.REFUND_HALF_HOURS:
        return 50
    return 0


class BookingAgent(Agent):
    name = "BookingAgent"
    counters = ["bookings_accepted", "bookings_refused", "bookings_failed", "tickets_sold",
                "cancellations", "refunds_total_lkr", "event_cancel_refunds", "holds_granted", "holds_refused"]

    # ---------- atomic inventory (the anti-overselling mechanism) ----------
    # Each change is ONE conditional UPDATE. The database applies it atomically,
    # so two simultaneous requests for the last seat cannot both succeed.
    def _reserve(self, db, cat_id, q):
        r = db.execute(update(TicketCategory).where(
            TicketCategory.id == cat_id,
            TicketCategory.sold + TicketCategory.held + q <= TicketCategory.quantity,
        ).values(sold=TicketCategory.sold + q).execution_options(synchronize_session=False))
        return r.rowcount == 1

    def _unreserve(self, db, cat_id, q):
        db.execute(update(TicketCategory).where(TicketCategory.id == cat_id, TicketCategory.sold >= q)
                   .values(sold=TicketCategory.sold - q).execution_options(synchronize_session=False))

    def _refuse(self, db, msg, reason, **extra):
        self.bump(db, "bookings_refused")
        return self.reply(msg, P.REFUSE, reason=reason, **extra)

    # ---------- buying ----------
    def do_book(self, db, msg):
        c = msg.content
        try:
            q = int(c.get("quantity", 0))
        except (TypeError, ValueError):
            q = 0
        if q < 1 or q > config.MAX_TICKETS_PER_REQUEST:                                   # B1
            return self._refuse(db, msg, f"You can book 1 to {config.MAX_TICKETS_PER_REQUEST} tickets at a time.")
        cat = db.get(TicketCategory, c.get("category_id"))
        if cat is None:
            return self._refuse(db, msg, "That ticket category does not exist.")
        ev = cat.event
        if ev.status != "published":                                                       # B2
            return self._refuse(db, msg, "This event is not open for booking.")
        if clock.now() >= ev.start:                                                        # B3
            return self._refuse(db, msg, "Ticket sales closed when the event started.")
        have = db.scalar(select(func.coalesce(func.sum(Booking.quantity), 0)).where(
            Booking.user_id == c.get("user_id"), Booking.event_id == ev.id, Booking.status == "confirmed"))
        if have + q > config.MAX_TICKETS_PER_USER_EVENT:                                   # B4
            return self._refuse(db, msg, f"Limit is {config.MAX_TICKETS_PER_USER_EVENT} tickets per person "
                                         f"per event; you already hold {have}.")
        if not self._reserve(db, cat.id, q):                                               # B5
            db.refresh(cat)
            return self._refuse(db, msg, f"Only {cat.available} ticket(s) left in '{cat.name}'.",
                                tickets_left=cat.available, waitlist_available=True)
        return self._complete(db, msg, c.get("user_id"), cat, ev, q, lambda: self._unreserve(db, cat.id, q))

    def _complete(self, db, msg, user_id, cat, ev, q, undo):
        booking = Booking(user_id=user_id, event_id=ev.id, category_id=cat.id, quantity=q,
                          unit_price=cat.price, total=round(cat.price * q, 2),
                          payment_ref="SIM-" + secrets.token_hex(5).upper(), status="confirmed")
        db.add(booking)
        db.flush()
        rep = self.ask(db, "TicketAgent", "issue_tickets", msg.conversation_id, booking_id=booking.id, quantity=q)
        if rep.performative != P.AGREE:                    # compensate: nothing may stay half-done
            undo()
            db.delete(booking)
            db.flush()
            self.bump(db, "bookings_failed")
            return self.reply(msg, P.FAILURE, reason="Ticket issuing failed, so the booking was rolled back.")
        self.bump(db, "bookings_accepted")
        self.bump(db, "tickets_sold", q)
        self.broadcast(db, msg.conversation_id, "booking_confirmed", booking_id=booking.id, user_id=user_id,
                       event_id=ev.id, event_title=ev.title, quantity=q, total=booking.total)
        return self.reply(msg, P.AGREE, booking_id=booking.id, quantity=q, total=booking.total,
                          payment_ref=booking.payment_ref)

    # ---------- waitlist seat holds (requested by WaitlistAgent) ----------
    def do_hold_seats(self, db, msg):
        cid, q = msg.content.get("category_id"), int(msg.content.get("quantity", 0))
        if q < 1:
            return self.reply(msg, P.REFUSE, reason="Quantity must be at least 1.", tickets_left=0)
        r = db.execute(update(TicketCategory).where(
            TicketCategory.id == cid,
            TicketCategory.sold + TicketCategory.held + q <= TicketCategory.quantity,
        ).values(held=TicketCategory.held + q).execution_options(synchronize_session=False))
        if r.rowcount == 1:
            self.bump(db, "holds_granted")
            return self.reply(msg, P.AGREE, held=q)
        cat = db.get(TicketCategory, cid)
        if cat:
            db.refresh(cat)
        self.bump(db, "holds_refused")
        return self.reply(msg, P.REFUSE, reason="Not enough free seats to hold.",
                          tickets_left=cat.available if cat else 0)

    def do_release_hold(self, db, msg):
        cid, q = msg.content.get("category_id"), int(msg.content.get("quantity", 0))
        db.execute(update(TicketCategory).where(TicketCategory.id == cid, TicketCategory.held >= q)
                   .values(held=TicketCategory.held - q).execution_options(synchronize_session=False))
        return self.reply(msg, P.AGREE, released=q)

    def do_book_from_hold(self, db, msg):
        c = msg.content
        cid, q = c.get("category_id"), int(c.get("quantity", 0))
        r = db.execute(update(TicketCategory).where(TicketCategory.id == cid, TicketCategory.held >= q)
                       .values(held=TicketCategory.held - q, sold=TicketCategory.sold + q)
                       .execution_options(synchronize_session=False))
        if r.rowcount != 1:
            return self._refuse(db, msg, "The seat hold no longer exists.")
        cat = db.get(TicketCategory, cid)

        def undo():
            db.execute(update(TicketCategory).where(TicketCategory.id == cid)
                       .values(held=TicketCategory.held + q, sold=TicketCategory.sold - q)
                       .execution_options(synchronize_session=False))
        return self._complete(db, msg, c.get("user_id"), cat, cat.event, q, undo)

    # ---------- cancelling ----------
    def do_cancel_booking(self, db, msg):
        c = msg.content
        b = db.get(Booking, c.get("booking_id"))
        if b is None or b.user_id != c.get("user_id"):
            return self.reply(msg, P.REFUSE, reason="Booking not found.")
        if b.status != "confirmed":
            return self.reply(msg, P.REFUSE, reason="This booking is already cancelled.")
        now = clock.now()
        if now >= b.event.start:                                                           # B6
            return self.reply(msg, P.REFUSE, reason="Bookings cannot be cancelled after the event has started.")
        if db.scalar(select(func.count(Ticket.id)).where(Ticket.booking_id == b.id, Ticket.status == "used")):
            return self.reply(msg, P.REFUSE, reason="A ticket in this booking was already used.")
        pct = refund_percent((b.event.start - now).total_seconds() / 3600)                 # B7
        rep = self.ask(db, "TicketAgent", "cancel_tickets", msg.conversation_id, booking_id=b.id)
        if rep.performative != P.AGREE:
            return self.reply(msg, P.FAILURE, reason="Tickets could not be cancelled: " + str(rep.content.get("reason")))
        refund = round(b.total * pct / 100, 2)
        b.status, b.refund_amount, b.cancelled_at = "cancelled", refund, now
        b.refund_ref = ("REF-" + secrets.token_hex(5).upper()) if refund > 0 else None
        b.cancel_reason = f"Cancelled by attendee ({pct}% refund)"
        db.flush()
        self._unreserve(db, b.category_id, b.quantity)
        self.bump(db, "cancellations")
        self.bump(db, "refunds_total_lkr", int(round(refund)))
        self.broadcast(db, msg.conversation_id, "booking_cancelled", booking_id=b.id, user_id=b.user_id,
                       event_title=b.event.title, refund=refund, percent=pct)
        self.broadcast(db, msg.conversation_id, "tickets_released", event_id=b.event_id,
                       category_id=b.category_id, quantity=b.quantity)
        return self.reply(msg, P.AGREE, refund_amount=refund, refund_percent=pct)

    # ---------- reactive: an event was cancelled -> full refunds (rule B8) ----------
    def on_event_cancelled(self, db, msg):
        eid = msg.content["event_id"]
        bookings = db.scalars(select(Booking).where(Booking.event_id == eid, Booking.status == "confirmed")).all()
        for b in bookings:
            self.ask(db, "TicketAgent", "cancel_tickets", msg.conversation_id, booking_id=b.id, force=True)
            b.status, b.refund_amount, b.cancelled_at = "cancelled", b.total, clock.now()
            b.refund_ref = "REF-" + secrets.token_hex(5).upper()
            b.cancel_reason = "Event cancelled - full refund"
            db.flush()
            self.bump(db, "event_cancel_refunds")
            self.bump(db, "refunds_total_lkr", int(round(b.total)))
            self.broadcast(db, msg.conversation_id, "booking_refunded", booking_id=b.id, user_id=b.user_id,
                           event_title=b.event.title, refund=b.total)
