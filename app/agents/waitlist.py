"""WaitlistAgent – goal: pass freed seats to waiting customers, fairly, with timed holds."""
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app import clock, config
from app.agents.base import Agent
from app.bus import Performative as P
from app.models import Event, TicketCategory, WaitlistEntry


class WaitlistAgent(Agent):
    name = "WaitlistAgent"
    counters = ["joined", "holds_offered", "holds_claimed", "holds_expired", "sweeps_with_work"]

    def do_join(self, db, msg):
        c = msg.content
        q = int(c.get("quantity", 0))
        cat = db.get(TicketCategory, c.get("category_id"))
        if q < 1 or q > config.MAX_TICKETS_PER_REQUEST:                                    # W1
            return self.reply(msg, P.REFUSE, reason=f"Waitlist requests are for 1 to {config.MAX_TICKETS_PER_REQUEST} tickets.")
        if cat is None or cat.event_id != c.get("event_id"):
            return self.reply(msg, P.REFUSE, reason="Ticket category not found.")
        ev = cat.event
        if ev.status != "published" or clock.now() >= ev.start:                            # W2
            return self.reply(msg, P.REFUSE, reason="This event is no longer open.")
        if cat.available >= q:                                                             # W3
            return self.reply(msg, P.REFUSE, reason=f"{cat.available} ticket(s) are available now, so book directly.")
        dup = db.scalar(select(WaitlistEntry.id).where(
            WaitlistEntry.user_id == c.get("user_id"), WaitlistEntry.category_id == cat.id,
            WaitlistEntry.status.in_(("waiting", "held"))))
        if dup:                                                                            # W4
            return self.reply(msg, P.REFUSE, reason="You are already on the waitlist for this category.")
        entry = WaitlistEntry(event_id=ev.id, category_id=cat.id, user_id=c.get("user_id"), quantity=q)
        db.add(entry)
        db.flush()
        pos = db.scalar(select(func.count(WaitlistEntry.id)).where(
            WaitlistEntry.category_id == cat.id, WaitlistEntry.status == "waiting", WaitlistEntry.id <= entry.id))
        self.bump(db, "joined")
        return self.reply(msg, P.AGREE, entry_id=entry.id, position=pos)

    def _allocate(self, db, conv, category_id):
        """Rule W5: first come, first served; an entry too big for the free seats is skipped."""
        cat = db.get(TicketCategory, category_id)
        if cat is None or cat.event.status != "published" or clock.now() >= cat.event.start:
            return 0
        entries = db.scalars(select(WaitlistEntry).where(
            WaitlistEntry.category_id == category_id, WaitlistEntry.status == "waiting")
            .order_by(WaitlistEntry.created_at, WaitlistEntry.id)).all()
        offered = 0
        for en in entries:
            rep = self.ask(db, "BookingAgent", "hold_seats", conv, category_id=category_id, quantity=en.quantity)
            if rep.performative == P.AGREE:
                en.status = "held"
                en.hold_expires_at = clock.now() + timedelta(minutes=config.HOLD_MINUTES)   # W6
                db.flush()
                self.bump(db, "holds_offered")
                offered += 1
                self.broadcast(db, conv, "waitlist_offer", entry_id=en.id, user_id=en.user_id,
                               event_id=en.event_id, event_title=cat.event.title, quantity=en.quantity,
                               minutes=config.HOLD_MINUTES)
            elif rep.content.get("tickets_left", 0) <= 0:
                break
        return offered

    def do_claim(self, db, msg):
        c = msg.content
        en = db.get(WaitlistEntry, c.get("entry_id"))
        if en is None or en.user_id != c.get("user_id"):
            return self.reply(msg, P.REFUSE, reason="Waitlist entry not found.")
        if en.status != "held":
            return self.reply(msg, P.REFUSE, reason="There is no active seat hold for this entry.")
        if en.hold_expires_at <= clock.now():                                              # W7
            return self.reply(msg, P.REFUSE, reason="Your seat hold has expired and was passed to the next person.")
        rep = self.ask(db, "BookingAgent", "book_from_hold", msg.conversation_id,
                       user_id=en.user_id, category_id=en.category_id, quantity=en.quantity)
        if rep.performative != P.AGREE:
            return self.reply(msg, P.FAILURE, reason=str(rep.content.get("reason", "Booking failed.")))
        en.status = "claimed"
        self.bump(db, "holds_claimed")
        return self.reply(msg, P.AGREE, booking_id=rep.content.get("booking_id"))

    def do_leave(self, db, msg):
        c = msg.content
        en = db.get(WaitlistEntry, c.get("entry_id"))
        if en is None or en.user_id != c.get("user_id") or en.status not in ("waiting", "held"):
            return self.reply(msg, P.REFUSE, reason="Waitlist entry not found.")
        was_held = en.status == "held"
        en.status = "cancelled"
        if was_held:
            self.ask(db, "BookingAgent", "release_hold", msg.conversation_id,
                     category_id=en.category_id, quantity=en.quantity)
            self._allocate(db, msg.conversation_id, en.category_id)
        return self.reply(msg, P.AGREE, status="cancelled")

    def do_sweep(self, db, msg):
        return self.reply(msg, P.AGREE, expired=self.tick(db, msg.conversation_id))

    # ---------- reactive: seats were freed ----------
    def on_tickets_released(self, db, msg):
        self._allocate(db, msg.conversation_id, msg.content["category_id"])

    def on_event_cancelled(self, db, msg):
        for en in db.scalars(select(WaitlistEntry).where(
                WaitlistEntry.event_id == msg.content["event_id"], WaitlistEntry.status.in_(("waiting", "held")))):
            en.status = "cancelled"

    # ---------- proactive: runs on a timer, with no user action (rule W8) ----------
    def tick(self, db, conv=None):
        conv = conv or "sweep-" + uuid.uuid4().hex[:6]
        expired = db.scalars(select(WaitlistEntry).where(
            WaitlistEntry.status == "held", WaitlistEntry.hold_expires_at <= clock.now())).all()
        cats = set()
        for en in expired:
            self.ask(db, "BookingAgent", "release_hold", conv, category_id=en.category_id, quantity=en.quantity)
            en.status = "expired"
            cats.add(en.category_id)
            self.bump(db, "holds_expired")
            self.broadcast(db, conv, "waitlist_expired", entry_id=en.id, user_id=en.user_id,
                           event_title=en.event.title)
        for cid in cats:                       # re-offer the freed seats to the next people in line
            self._allocate(db, conv, cid)
        if expired:
            self.bump(db, "sweeps_with_work")
        return len(expired)
