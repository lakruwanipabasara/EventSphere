from datetime import timedelta

from app import clock
from app.bus import Performative as P
from app.models import Event, Ticket
from tests.conftest import ask, make_event, make_user, make_venue


def _book_then_move_event(db, start_in_hours):
    v, u = make_venue(db), make_user(db)
    ev = make_event(db, v, start_in_hours=24 * 10)
    ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=1)
    ev.start = clock.now() + timedelta(hours=start_in_hours)
    ev.end = ev.start + timedelta(hours=3)
    db.commit()
    return ev, db.query(Ticket).first()


def test_double_check_in_is_rejected(db):
    ev, t = _book_then_move_event(db, 1)
    first = ask(db, "TicketAgent", "check_in", code=t.code, event_id=ev.id, staff_id=1)
    second = ask(db, "TicketAgent", "check_in", code=t.code, event_id=ev.id, staff_id=1)
    assert first.performative == P.AGREE
    from app.models import MessageLog
    assert all(t.code not in m.content for m in db.query(MessageLog).all())   # codes are redacted in the log
    assert second.performative == P.REFUSE and second.content["reason"] == "already_used"


def test_wrong_event_cancelled_unknown_and_early_scans_are_rejected(db):
    ev, t = _book_then_move_event(db, 1)
    other = make_event(db, make_venue(db, name="Other"), title="Other event")
    assert ask(db, "TicketAgent", "check_in", code=t.code, event_id=other.id).content["reason"] == "wrong_event"
    assert ask(db, "TicketAgent", "check_in", code="nope", event_id=ev.id).content["reason"] == "unknown_ticket"
    ev.start = clock.now() + timedelta(hours=5)      # check-in opens only 2 h before start
    ev.end = ev.start + timedelta(hours=2)
    db.commit()
    assert ask(db, "TicketAgent", "check_in", code=t.code, event_id=ev.id).content["reason"] == "outside_checkin_time"
    ask(db, "TicketAgent", "cancel_tickets", booking_id=t.booking_id)
    ev.start = clock.now() + timedelta(hours=1)
    db.commit()
    assert ask(db, "TicketAgent", "check_in", code=t.code, event_id=ev.id).content["reason"] == "ticket_cancelled"


def test_repeated_scans_flag_suspicious_ticket_and_notify_admin(db):
    from app.models import Notification
    ev, t = _book_then_move_event(db, 1)
    ask(db, "TicketAgent", "check_in", code=t.code, event_id=ev.id)
    last = None
    for _ in range(2):
        last = ask(db, "TicketAgent", "check_in", code=t.code, event_id=ev.id)
    assert last.content["suspicious"] is True
    assert db.query(Notification).filter_by(role="admin").count() == 1
