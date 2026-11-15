import json
from datetime import timedelta

from app import clock
from app.agents import bus
from app.bus import Performative as P
from app.models import AgentState, MessageLog, Notification, TicketCategory, WaitlistEntry
from app.db import SessionLocal
from tests.conftest import ask, make_event, make_user, make_venue


def _sold_out(db):
    v = make_venue(db)
    ev = make_event(db, v, tickets=(("General", 100.0, 1),))
    cid = ev.categories[0].id
    owner, w1, w2 = make_user(db, "o@x.test"), make_user(db, "w1@x.test"), make_user(db, "w2@x.test")
    booked = ask(db, "BookingAgent", "book", user_id=owner.id, category_id=cid, quantity=1)
    return ev, cid, owner, w1, w2, booked


def test_waitlist_holds_freed_seat_for_first_in_line_and_claim_books_it(db):
    ev, cid, owner, w1, w2, booked = _sold_out(db)
    assert ask(db, "WaitlistAgent", "join", user_id=w1.id, event_id=ev.id, category_id=cid, quantity=1).performative == P.AGREE
    assert ask(db, "WaitlistAgent", "join", user_id=w2.id, event_id=ev.id, category_id=cid, quantity=1).content["position"] == 2
    ask(db, "BookingAgent", "cancel_booking", user_id=owner.id, booking_id=booked.content["booking_id"])
    first = db.query(WaitlistEntry).filter_by(user_id=w1.id).one()
    assert first.status == "held" and first.hold_expires_at is not None
    db.expire_all()
    assert db.get(TicketCategory, cid).available == 0          # seat is held, nobody else can buy it
    rep = ask(db, "WaitlistAgent", "claim", user_id=w1.id, entry_id=first.id)
    assert rep.performative == P.AGREE and rep.content["booking_id"]


def test_timer_sweep_expires_hold_and_reoffers_to_next_person(db, monkeypatch):
    ev, cid, owner, w1, w2, booked = _sold_out(db)
    for u in (w1, w2):
        ask(db, "WaitlistAgent", "join", user_id=u.id, event_id=ev.id, category_id=cid, quantity=1)
    ask(db, "BookingAgent", "cancel_booking", user_id=owner.id, booking_id=booked.content["booking_id"])
    real = clock.now
    monkeypatch.setattr(clock, "now", lambda: real() + timedelta(minutes=30))   # fast-forward past the hold
    with SessionLocal() as s:                       # the timer acts with no user request
        assert bus.agents["WaitlistAgent"].tick(s) == 1
        s.commit()
    db.expire_all()
    e1 = db.query(WaitlistEntry).filter_by(user_id=w1.id).one()
    e2 = db.query(WaitlistEntry).filter_by(user_id=w2.id).one()
    assert e1.status == "expired" and e2.status == "held"
    late = ask(db, "WaitlistAgent", "claim", user_id=w1.id, entry_id=e1.id)
    assert late.performative == P.REFUSE


def test_waitlist_refuses_when_seats_are_available(db):
    v, u = make_venue(db), make_user(db)
    ev = make_event(db, v)
    rep = ask(db, "WaitlistAgent", "join", user_id=u.id, event_id=ev.id, category_id=ev.categories[0].id, quantity=1)
    assert rep.performative == P.REFUSE and "book directly" in rep.content["reason"]


def test_notification_agent_tells_the_right_user(db):
    v, u, other = make_venue(db), make_user(db), make_user(db, "other@x.test")
    ev = make_event(db, v)
    ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=2)
    mine = db.query(Notification).filter_by(user_id=u.id).all()
    assert len(mine) == 1 and "confirmed" in mine[0].message
    assert db.query(Notification).filter_by(user_id=other.id).count() == 0


def test_one_conversation_id_links_messages_across_agents(db):
    v, u = make_venue(db), make_user(db)
    ev = make_event(db, v)
    ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=1)
    conv = db.query(MessageLog).filter_by(receiver="BookingAgent", performative="REQUEST").one().conversation_id
    trail = db.query(MessageLog).filter_by(conversation_id=conv).order_by(MessageLog.id).all()
    pairs = {(m.sender, m.receiver, m.performative) for m in trail}
    assert ("BookingAgent", "TicketAgent", "REQUEST") in pairs
    assert ("TicketAgent", "BookingAgent", "AGREE") in pairs
    assert ("BookingAgent", "*", "INFORM") in pairs and ("BookingAgent", "test", "AGREE") in pairs


def test_agent_beliefs_are_persistent_across_sessions(db):
    v, u = make_venue(db), make_user(db)
    ev = make_event(db, v)
    ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=2)
    with SessionLocal() as fresh:        # a brand new session = what a restarted server would read
        row = fresh.get(AgentState, ("BookingAgent", "tickets_sold"))
        assert json.loads(row.value) == 2
