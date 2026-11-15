from app.models import Event, User
from tests.conftest import csrf_from, make_event, make_user, make_venue


def login(client, email, password="Password#1"):
    token = csrf_from(client.get("/login").text)
    return client.post("/login", data={"email": email, "password": password, "csrf_token": token},
                       follow_redirects=False)


def test_register_hashes_password_and_roles_guard_admin_pages(client, db):
    token = csrf_from(client.get("/register").text)
    r = client.post("/register", data={"name": "Sam", "email": "sam@x.test", "password": "Secret#123",
                                       "csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    user = db.query(User).filter_by(email="sam@x.test").one()
    assert user.password_hash != "Secret#123" and user.password_hash.startswith("$2")
    assert user.role == "attendee"
    assert client.get("/admin").status_code == 403           # attendee cannot open admin pages
    assert client.get("/staff/scanner").status_code == 403    # ...or the scanner


def test_anonymous_users_are_sent_to_login(client):
    r = client.get("/staff/scanner", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_post_without_csrf_token_is_rejected(client, db):
    make_user(db, "u@x.test")
    r = client.post("/login", data={"email": "u@x.test", "password": "Password#1"})
    assert r.status_code == 403


def test_agent_inbox_requires_key_and_returns_agent_reply(client, db):
    msg = {"sender": "tester", "receiver": "VenueAgent", "performative": "REQUEST",
           "content": {"action": "check_slot", "venue_id": 999, "start": "2030-01-01T10:00", "end": "2030-01-01T12:00"}}
    assert client.post("/api/agents/VenueAgent/inbox", json=msg).status_code == 401
    r = client.post("/api/agents/VenueAgent/inbox", json=msg, headers={"X-Agent-Key": "test-key"})
    assert r.status_code == 200 and r.json()["performative"] == "REFUSE"


def test_attendee_books_and_staff_scanner_admits_once_over_http(client, db):
    from datetime import timedelta
    from app import clock
    from app.models import Ticket
    attendee, staff = make_user(db, "att@x.test"), make_user(db, "staff@x.test", role="staff")
    ev = make_event(db, make_venue(db), start_in_hours=240)
    # attendee books through the web form
    login(client, "att@x.test")
    page = client.get(f"/events/{ev.id}")
    assert "Pay &amp; book" in page.text
    r = client.post(f"/events/{ev.id}/book", data={"category_id": ev.categories[0].id, "quantity": 1,
                                                    "csrf_token": csrf_from(page.text)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/bookings/")
    tid = db.query(Ticket).one().id
    assert client.get(f"/tickets/{tid}/qr.png").headers["content-type"] == "image/png"
    # the event begins soon, staff scans
    db.expire_all()
    ev = db.get(Event, ev.id)
    ev.start, ev.end = clock.now() + timedelta(hours=1), clock.now() + timedelta(hours=3)
    code = db.query(Ticket).one().code
    db.commit()
    staff_client = type(client)(client.app)
    login(staff_client, "staff@x.test")
    tok = csrf_from(staff_client.get("/staff/scanner").text)
    h = {"X-CSRF-Token": tok}
    ok = staff_client.post("/staff/scan", json={"code": code, "event_id": ev.id}, headers=h).json()
    again = staff_client.post("/staff/scan", json={"code": code, "event_id": ev.id}, headers=h).json()
    assert ok["ok"] is True and again["ok"] is False and again["reason"] == "already_used"
