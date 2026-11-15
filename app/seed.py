"""Demo data: three demo accounts, venues and events (only when the database is empty)."""
from datetime import timedelta

from sqlalchemy import func, select

from app import clock, config
from app.models import Event, TicketCategory, User, Venue
from app.security import hash_password


def seed_demo(db):
    if db.scalar(select(func.count(User.id))):
        return
    db.add_all([
        User(name="Demo Admin", email="admin@eventsphere.test", role="admin",
             password_hash=hash_password(config.DEMO_ADMIN_PASSWORD)),
        User(name="Gate Staff", email="staff@eventsphere.test", role="staff",
             password_hash=hash_password(config.DEMO_STAFF_PASSWORD)),
        User(name="Demo Attendee", email="attendee@eventsphere.test", role="attendee",
             password_hash=hash_password(config.DEMO_ATTENDEE_PASSWORD)),
    ])
    hall = Venue(name="Main Auditorium", location="Block A, Campus", capacity=400, hourly_rate=12000,
                 description="Tiered auditorium with stage lighting and sound.", open_for_hire=True)
    lab = Venue(name="Innovation Lab", location="Block C, Level 2", capacity=60, hourly_rate=4000,
                description="Flexible workshop room with projector and whiteboards.", open_for_hire=True)
    lawn = Venue(name="Lakeside Lawn", location="Behind the library", capacity=250, hourly_rate=6000,
                 description="Open-air lawn for evening shows.", open_for_hire=False)
    db.add_all([hall, lab, lawn])
    db.flush()
    now = clock.now().replace(minute=0, second=0, microsecond=0)

    def event(title, cat, venue, start, hours, desc, tickets):
        ev = Event(title=title, category=cat, venue_id=venue.id, start=start, end=start + timedelta(hours=hours),
                   description=desc, status="published")
        ev.categories = [TicketCategory(name=n, price=p, quantity=q) for n, p, q in tickets]
        db.add(ev)

    event("Open Mic Tonight", "Concert", lawn, now + timedelta(hours=2), 3,
          "Check-in is open right now, so staff can test the scanner.", [("General", 500, 40)])
    event("Jazz Under the Stars", "Concert", hall, (now + timedelta(days=8)).replace(hour=18), 3,
          "An evening of live jazz.", [("Standard", 2500, 200), ("VIP", 6000, 30)])
    event("Intro to Agent-Based Computing", "Workshop", lab, (now + timedelta(days=3)).replace(hour=10), 4,
          "Hands-on workshop on cooperating software agents.", [("Student", 800, 30), ("Public", 1500, 20)])
    event("Poetry Night (tiny room - try the waitlist)", "Seminar", lab,
          (now + timedelta(days=5)).replace(hour=18), 2, "Only five seats in total.", [("General", 300, 5)])
    db.commit()
