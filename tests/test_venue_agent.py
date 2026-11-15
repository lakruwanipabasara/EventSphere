from datetime import timedelta

from app import clock
from app.bus import Performative as P
from tests.conftest import ask, make_event, make_user, make_venue


def test_venue_refuses_overlap_and_buffer_but_accepts_after_buffer(db):
    v = make_venue(db)
    ev = make_event(db, v, start_in_hours=100, hours=2)
    # starts 10 minutes after the first event ends: inside the 30 min turnaround buffer
    s = ev.end + timedelta(minutes=10)
    rep = ask(db, "VenueAgent", "check_slot", venue_id=v.id, start=s.isoformat(), end=(s + timedelta(hours=1)).isoformat())
    assert rep.performative == P.REFUSE
    assert "not free" in rep.content["reason"] and rep.content["suggested_slots"]
    # 45 minutes after: allowed
    s2 = ev.end + timedelta(minutes=45)
    rep = ask(db, "VenueAgent", "check_slot", venue_id=v.id, start=s2.isoformat(), end=(s2 + timedelta(hours=1)).isoformat())
    assert rep.performative == P.AGREE


def test_venue_refuses_more_tickets_than_capacity(db):
    v = make_venue(db, capacity=50)
    s = clock.now() + timedelta(days=20)
    rep = ask(db, "VenueAgent", "check_slot", venue_id=v.id, start=s.isoformat(),
              end=(s + timedelta(hours=2)).isoformat(), total_tickets=51)
    assert rep.performative == P.REFUSE and "capacity" in rep.content["reason"]


def _hire(db, v, user, start, hours, guests):
    return ask(db, "VenueAgent", "hire_request", user_id=user.id, venue_id=v.id, start=start.isoformat(),
               end=(start + timedelta(hours=hours)).isoformat(), guests=guests, purpose="Farewell")


def _next_weekday(days_ahead=10, hour=10, weekend=False):
    d = clock.now().replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    while (d.weekday() >= 5) != weekend:
        d += timedelta(days=1)
    return d


def test_hire_routine_confirmed_then_clash_gets_alternatives(db):
    v, other = make_venue(db, capacity=100), make_venue(db, name="Lab", capacity=100)
    u = make_user(db)
    start = _next_weekday()
    first = _hire(db, v, u, start, 2, 30)
    assert first.performative == P.AGREE and first.content["decision"] == "confirmed"
    second = _hire(db, v, u, start, 2, 30)
    assert second.performative == P.PROPOSE
    assert second.content["alternative_times"] and second.content["alternative_venues"]
    assert second.content["alternative_venues"][0]["venue_id"] == other.id


def test_hire_large_request_is_escalated_to_admin(db):
    v = make_venue(db, capacity=100)
    u = make_user(db)
    rep = _hire(db, v, u, _next_weekday(), 2, 95)       # 95% of capacity
    assert rep.performative == P.INFORM and rep.content["decision"] == "escalated"


def test_hire_weekend_costs_more_than_weekday(db):
    v = make_venue(db, rate=1000)
    u = make_user(db)
    wk = _hire(db, v, u, _next_weekday(10, 10, False), 2, 10)
    we = _hire(db, v, u, _next_weekday(20, 10, True), 2, 10)
    assert wk.content["price"] == 2000
    assert we.content["price"] == 2500
