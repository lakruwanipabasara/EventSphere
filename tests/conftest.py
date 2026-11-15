import os
import re
import tempfile
from datetime import timedelta

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["SEED_DEMO"] = "false"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["AGENT_API_KEY"] = "test-key"
os.environ["SECRET_KEY"] = "test-secret"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import clock  # noqa: E402
from app.agents import bus  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Event, TicketCategory, User, Venue  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bus.init_state(db)
        db.commit()
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def make_user(db, email="a@x.test", role="attendee", password="Password#1"):
    u = User(name=email.split("@")[0].title(), email=email, role=role, password_hash=hash_password(password))
    db.add(u)
    db.commit()
    return u


def make_venue(db, name="Hall", capacity=100, rate=1000, hire=True):
    v = Venue(name=name, location="Campus", capacity=capacity, hourly_rate=rate, open_for_hire=hire)
    db.add(v)
    db.commit()
    return v


def make_event(db, venue, start_in_hours=24 * 10, hours=2, tickets=((("General", 100.0, 10)),), status="published",
               title="Test Event"):
    start = clock.now().replace(second=0, microsecond=0) + timedelta(hours=start_in_hours)
    ev = Event(title=title, category="Concert", venue_id=venue.id, start=start, end=start + timedelta(hours=hours),
               status=status)
    ev.categories = [TicketCategory(name=n, price=p, quantity=q) for n, p, q in tickets]
    db.add(ev)
    db.commit()
    return ev


def ask(db, receiver, action, sender="test", **content):
    rep = bus.request(db, sender, receiver, action, content)
    db.commit()
    return rep


def csrf_from(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    return TestClient(app)
