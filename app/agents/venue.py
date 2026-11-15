"""VenueAgent – goal: keep every venue's calendar free of clashes."""
from datetime import datetime, timedelta

from sqlalchemy import select

from app import clock, config
from app.agents.base import Agent
from app.bus import Performative as P
from app.models import Event, HireRequest, Venue

FMT = "%d %b %H:%M"


def parse(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def iso(d):
    return d.isoformat(timespec="minutes")


class VenueAgent(Agent):
    name = "VenueAgent"
    counters = ["slots_accepted", "slots_refused", "hires_confirmed", "hires_escalated",
                "hires_proposed", "hires_refused", "hires_approved", "hires_rejected", "slots_released"]

    def __init__(self):
        super().__init__()
        self.buffer = timedelta(minutes=config.BUFFER_MINUTES)

    # ---------- calendar helpers ----------
    def _busy(self, db, venue_id, lo, hi, exclude_event=None, exclude_hire=None):
        out = []
        events = db.scalars(select(Event).where(
            Event.venue_id == venue_id, Event.status != "cancelled", Event.start < hi, Event.end > lo))
        for e in events:
            if e.id != exclude_event:
                out.append((e.start, e.end, f"event '{e.title}' {e.start:{FMT}}-{e.end:%H:%M}"))
        hires = db.scalars(select(HireRequest).where(
            HireRequest.venue_id == venue_id, HireRequest.status.in_(("confirmed", "pending")),
            HireRequest.start < hi, HireRequest.end > lo))
        for h in hires:
            if h.id != exclude_hire:
                out.append((h.start, h.end, f"private hire {h.start:{FMT}}-{h.end:%H:%M}"))
        return out

    def _clash(self, busy, s, e):
        """Rule V4: two activities need a turnaround buffer between them."""
        return [lbl for bs, be, lbl in busy if bs < e + self.buffer and be > s - self.buffer]

    def _window(self, db, venue_id, s, **kw):
        return self._busy(db, venue_id, s - timedelta(days=2), s + timedelta(days=8), **kw)

    def _alt_times(self, busy, start, end, limit=3, open_h=0, close_h=24, min_start=None):
        dur = end - start
        day0 = start.replace(hour=0, minute=0, second=0, microsecond=0)
        cands = []
        for d in range(-1, 7):
            base = day0 + timedelta(days=d)
            limit_t = base + timedelta(hours=close_h)
            t = base + timedelta(hours=open_h)
            while t + dur <= limit_t:
                if t != start and (min_start is None or t >= min_start) and not self._clash(busy, t, t + dur):
                    cands.append((abs((t - start).total_seconds()), t))
                t += timedelta(minutes=30)
        cands.sort()
        chosen = []
        for _, t in cands:   # spread suggestions at least 2 hours apart
            if all(abs((t - c).total_seconds()) >= 7200 for c in chosen):
                chosen.append(t)
            if len(chosen) == limit:
                break
        return [{"start": iso(t), "end": iso(t + dur)} for t in sorted(chosen)]

    def price(self, venue, s, e):
        """Rule V8: hourly rate, +25 % at weekends, +15 % for blocks starting at/after 18:00."""
        base = weekend = peak = 0.0
        t = s
        while t < e:
            half = venue.hourly_rate / 2
            base += half
            if t.weekday() >= 5:
                weekend += half * config.WEEKEND_SURCHARGE
            if t.hour >= config.PEAK_FROM_HOUR:
                peak += half * config.PEAK_SURCHARGE
            t += timedelta(minutes=30)
        return {"base": round(base, 2), "weekend_surcharge": round(weekend, 2),
                "peak_surcharge": round(peak, 2), "total": round(base + weekend + peak, 2)}

    # ---------- event slot decisions ----------
    def do_check_slot(self, db, msg):
        c = msg.content

        def refuse(reason, **extra):
            self.bump(db, "slots_refused")
            return self.reply(msg, P.REFUSE, reason=reason, **extra)

        venue = db.get(Venue, c.get("venue_id"))
        if venue is None:
            return refuse("Venue does not exist.")
        try:
            s, e = parse(c["start"]), parse(c["end"])
        except (KeyError, ValueError, TypeError):
            return refuse("Start or end time is not valid.")
        if e <= s:
            return refuse("End time must be after the start time.")
        total = int(c.get("total_tickets", 0))
        if total > venue.capacity:                                   # V3
            return refuse(f"Total tickets ({total}) exceed the capacity of {venue.name} ({venue.capacity}).")
        busy = self._window(db, venue.id, s, exclude_event=c.get("exclude_event_id"))
        clashes = self._clash(busy, s, e)                            # V4
        if clashes:
            slots = self._alt_times(busy, s, e)
            return refuse(f"{venue.name} is not free ({config.BUFFER_MINUTES} min turnaround buffer included): "
                          + "; ".join(clashes) + ".", conflicts=clashes, suggested_slots=slots)
        self.bump(db, "slots_accepted")
        return self.reply(msg, P.AGREE, venue_id=venue.id, capacity=venue.capacity)

    # ---------- private hire ----------
    def do_hire_request(self, db, msg):
        c = msg.content
        now = clock.now()

        def refuse(reason):
            self.bump(db, "hires_refused")
            return self.reply(msg, P.REFUSE, reason=reason)

        venue = db.get(Venue, c.get("venue_id"))
        if venue is None:
            return refuse("Venue does not exist.")                                  # H1
        if not venue.open_for_hire:
            return refuse(f"{venue.name} is not open for private hire.")
        try:
            s, e = parse(c["start"]), parse(c["end"])
            guests = int(c["guests"])
        except (KeyError, ValueError, TypeError):
            return refuse("Date, time or guest count is not valid.")
        secs = (e - s).total_seconds()
        hours = secs / 3600
        if secs <= 0 or secs % 1800:
            return refuse("End must be after start, in 30-minute steps.")           # H2
        if hours < config.HIRE_MIN_HOURS or hours > config.HIRE_MAX_HOURS:
            return refuse(f"Hire must last {config.HIRE_MIN_HOURS} to {config.HIRE_MAX_HOURS} hours.")
        if s < now + timedelta(hours=config.HIRE_MIN_NOTICE_HOURS):                 # H3
            return refuse(f"Hire needs at least {config.HIRE_MIN_NOTICE_HOURS} hours' notice.")
        close = s.replace(hour=config.HIRE_CLOSE_HOUR, minute=0, second=0, microsecond=0)
        if s.date() != e.date() or s.hour < config.HIRE_OPEN_HOUR or e > close:     # H4
            return refuse(f"Hire must be on one day between {config.HIRE_OPEN_HOUR:02d}:00 "
                          f"and {config.HIRE_CLOSE_HOUR:02d}:00.")
        if guests < 1:
            return refuse("Guest count must be at least 1.")

        busy = self._window(db, venue.id, s)
        clashes = self._clash(busy, s, e)
        too_many = guests > venue.capacity
        if clashes or too_many:                                                     # H5 / H6
            reason = (f"{venue.name} holds {venue.capacity} guests; you need {guests}." if too_many
                      else f"{venue.name} is already booked then: " + "; ".join(clashes) + ".")
            times = [] if too_many else self._alt_times(
                busy, s, e, open_h=config.HIRE_OPEN_HOUR, close_h=config.HIRE_CLOSE_HOUR,
                min_start=now + timedelta(hours=config.HIRE_MIN_NOTICE_HOURS))
            venues = []
            for v in db.scalars(select(Venue).where(Venue.open_for_hire.is_(True), Venue.id != venue.id)):
                if v.capacity >= guests and not self._clash(self._window(db, v.id, s), s, e):
                    venues.append({"venue_id": v.id, "venue_name": v.name, "start": iso(s), "end": iso(e),
                                   "price": self.price(v, s, e)["total"]})
            if times or venues:
                self.bump(db, "hires_proposed")
                return self.reply(msg, P.PROPOSE, reason=reason, alternative_times=times,
                                  alternative_venues=venues[:3], venue_id=venue.id)
            return refuse(reason + " No alternatives are free.")

        quote = self.price(venue, s, e)                                             # H8
        reasons = []
        if hours > config.AUTO_APPROVE_MAX_HOURS:
            reasons.append(f"longer than {config.AUTO_APPROVE_MAX_HOURS} hours")
        if quote["total"] > config.AUTO_APPROVE_MAX_PRICE:
            reasons.append(f"price above Rs {config.AUTO_APPROVE_MAX_PRICE:,}")
        if guests > venue.capacity * config.AUTO_APPROVE_MAX_FILL:
            reasons.append(f"more than {int(config.AUTO_APPROVE_MAX_FILL * 100)}% of capacity")
        status = "pending" if reasons else "confirmed"                              # H7
        req = HireRequest(user_id=c.get("user_id"), venue_id=venue.id, start=s, end=e,
                          purpose=str(c.get("purpose", ""))[:300], guests=guests,
                          price=quote["total"], status=status,
                          reason="Needs admin review: " + ", ".join(reasons) if reasons else "Routine request")
        db.add(req)
        db.flush()
        data = dict(request_id=req.id, price=quote["total"], breakdown=quote, venue_name=venue.name,
                    user_id=req.user_id, start=iso(s), end=iso(e))
        if reasons:
            self.bump(db, "hires_escalated")
            self.broadcast(db, msg.conversation_id, "hire_requested", **data, reason=req.reason)
            return self.reply(msg, P.INFORM, decision="escalated", reason=req.reason, **data)
        self.bump(db, "hires_confirmed")
        self.broadcast(db, msg.conversation_id, "hire_decided", approved=True, **data)
        return self.reply(msg, P.AGREE, decision="confirmed", **data)

    def do_hire_decision(self, db, msg):
        c = msg.content
        req = db.get(HireRequest, c.get("request_id"))
        if req is None or req.status != "pending":
            return self.reply(msg, P.REFUSE, reason="Only pending requests can be decided.")
        data = dict(request_id=req.id, user_id=req.user_id, venue_name=req.venue.name,
                    start=iso(req.start), end=iso(req.end), price=req.price)
        if c.get("approve"):
            busy = self._window(db, req.venue_id, req.start, exclude_hire=req.id)
            clashes = self._clash(busy, req.start, req.end)
            if clashes:
                return self.reply(msg, P.REFUSE, reason="Cannot approve, the venue is now taken: "
                                  + "; ".join(clashes) + ".")
            req.status, req.reason = "confirmed", "Approved by admin"
            self.bump(db, "hires_approved")
        else:
            req.status, req.reason = "rejected", str(c.get("note") or "Rejected by admin")[:300]
            self.bump(db, "hires_rejected")
        db.flush()
        self.broadcast(db, msg.conversation_id, "hire_decided", approved=req.status == "confirmed", **data)
        return self.reply(msg, P.AGREE, status=req.status)

    # ---------- reactive behaviour ----------
    def on_event_cancelled(self, db, msg):
        """A cancelled event frees its slot (the calendar is derived from event status)."""
        self.bump(db, "slots_released")
