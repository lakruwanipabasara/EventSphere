import threading

from app import clock
from app.bus import Performative as P
from app.db import SessionLocal
from app.agents import bus
from app.models import Booking, TicketCategory, Ticket
from tests.conftest import ask, make_event, make_user, make_venue


def test_booking_refuses_more_than_six_tickets(db):
    u, v = make_user(db), make_venue(db)
    ev = make_event(db, v, tickets=(("General", 100.0, 50),))
    rep = ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=7)
    assert rep.performative == P.REFUSE and "1 to 6" in rep.content["reason"]


def test_booking_success_issues_unique_tickets(db):
    u, v = make_user(db), make_venue(db)
    ev = make_event(db, v, tickets=(("General", 100.0, 50),))
    rep = ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=3)
    assert rep.performative == P.AGREE and rep.content["payment_ref"].startswith("SIM-")
    codes = [t.code for t in db.query(Ticket).all()]
    assert len(codes) == 3 and len(set(codes)) == 3 and all(len(c) >= 20 for c in codes)


def test_booking_refuses_draft_and_started_events(db):
    u, v = make_user(db), make_venue(db)
    draft = make_event(db, v, status="draft", title="Draft")
    started = make_event(db, v, start_in_hours=-1, hours=3, title="Started")
    r1 = ask(db, "BookingAgent", "book", user_id=u.id, category_id=draft.categories[0].id, quantity=1)
    r2 = ask(db, "BookingAgent", "book", user_id=u.id, category_id=started.categories[0].id, quantity=1)
    assert r1.performative == P.REFUSE and r2.performative == P.REFUSE


def test_never_oversells_sequentially(db):
    v = make_venue(db)
    ev = make_event(db, v, tickets=(("General", 100.0, 3),))
    cid = ev.categories[0].id
    a, b = make_user(db, "a@x.test"), make_user(db, "b@x.test")
    assert ask(db, "BookingAgent", "book", user_id=a.id, category_id=cid, quantity=2).performative == P.AGREE
    rep = ask(db, "BookingAgent", "book", user_id=b.id, category_id=cid, quantity=2)
    assert rep.performative == P.REFUSE and rep.content["tickets_left"] == 1


def test_never_oversells_under_concurrency(db):
    """Eight people race for 3 seats at the same moment."""
    v = make_venue(db)
    ev = make_event(db, v, tickets=(("General", 100.0, 3),))
    cid = ev.categories[0].id
    users = [make_user(db, f"u{i}@x.test").id for i in range(8)]
    results, barrier = [], threading.Barrier(8)

    def worker(uid):
        with SessionLocal() as s:
            barrier.wait()
            rep = bus.request(s, f"user:{uid}", "BookingAgent", "book",
                              {"user_id": uid, "category_id": cid, "quantity": 1})
            s.commit()
            results.append(rep.performative)

    threads = [threading.Thread(target=worker, args=(u,)) for u in users]
    [t.start() for t in threads]
    [t.join() for t in threads]
    db.expire_all()
    cat = db.get(TicketCategory, cid)
    assert results.count(P.AGREE) == 3 and cat.sold == 3
    assert db.query(Booking).filter_by(status="confirmed").count() == 3
    assert db.query(Ticket).count() == 3


def test_refund_policy_by_notice_period(db):
    v = make_venue(db)
    u = make_user(db)
    for hours_ahead, pct in ((24 * 10, 100), (24 * 4, 50), (10, 0)):
        ev = make_event(db, v, start_in_hours=hours_ahead, title=f"E{hours_ahead}")
        booked = ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=1)
        rep = ask(db, "BookingAgent", "cancel_booking", user_id=u.id, booking_id=booked.content["booking_id"])
        assert rep.performative == P.AGREE and rep.content["refund_percent"] == pct, hours_ahead


def test_event_cancellation_triggers_full_refunds_via_broadcast(db):
    from app.bus import Message
    v, u = make_venue(db), make_user(db)
    ev = make_event(db, v)
    booked = ask(db, "BookingAgent", "book", user_id=u.id, category_id=ev.categories[0].id, quantity=2)
    ev.status = "cancelled"
    db.commit()
    bus.send(db, Message(sender="admin", receiver="*", performative=P.INFORM,
                         content={"event": "event_cancelled", "event_id": ev.id}))
    db.commit()
    b = db.get(Booking, booked.content["booking_id"])
    assert b.status == "cancelled" and b.refund_amount == b.total
    assert all(t.status == "cancelled" for t in b.tickets)
