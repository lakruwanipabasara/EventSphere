"""NotificationAgent – goal: tell the right person what affects them (listens to all INFORM events)."""
from app.agents.base import Agent
from app.models import Notification


class NotificationAgent(Agent):
    name = "NotificationAgent"
    counters = ["notifications_sent"]

    def _tell(self, db, text, user_id=None, role=None):
        db.add(Notification(user_id=user_id, role=role, message=text[:500]))
        db.flush()
        self.bump(db, "notifications_sent")

    def on_booking_confirmed(self, db, m):
        c = m.content
        self._tell(db, f"Booking #{c['booking_id']} confirmed: {c['quantity']} ticket(s) for "
                       f"{c['event_title']}, Rs {c['total']:,.2f} paid (simulated).", user_id=c["user_id"])

    def on_booking_cancelled(self, db, m):
        c = m.content
        self._tell(db, f"Booking #{c['booking_id']} for {c['event_title']} was cancelled. "
                       f"Refund: Rs {c['refund']:,.2f} ({c['percent']}%).", user_id=c["user_id"])

    def on_booking_refunded(self, db, m):
        c = m.content
        self._tell(db, f"{c['event_title']} was cancelled. Booking #{c['booking_id']} is fully refunded "
                       f"(Rs {c['refund']:,.2f}).", user_id=c["user_id"])

    def on_waitlist_offer(self, db, m):
        c = m.content
        self._tell(db, f"Good news! {c['quantity']} seat(s) are held for you at {c['event_title']}. "
                       f"Claim them within {c['minutes']} minutes.", user_id=c["user_id"])

    def on_waitlist_expired(self, db, m):
        c = m.content
        self._tell(db, f"Your seat hold for {c['event_title']} expired and was passed on.", user_id=c["user_id"])

    def on_hire_decided(self, db, m):
        c = m.content
        word = "approved" if c.get("approved") else "rejected"
        self._tell(db, f"Your hire request #{c['request_id']} for {c['venue_name']} was {word}. "
                       f"Price: Rs {c['price']:,.2f}.", user_id=c["user_id"])

    def on_hire_requested(self, db, m):
        c = m.content
        self._tell(db, f"Hire request #{c['request_id']} for {c['venue_name']} needs your review "
                       f"({c.get('reason', '')}).", role="admin")
        self._tell(db, f"Your hire request #{c['request_id']} is waiting for admin approval. "
                       f"Quoted price: Rs {c['price']:,.2f}.", user_id=c["user_id"])

    def on_suspicious_scan(self, db, m):
        c = m.content
        self._tell(db, f"Ticket #{c['ticket_id']} was scanned {c['scans']} times - possible shared screenshot.",
                   role="admin")
