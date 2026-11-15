# EventSphere

**A mobile-friendly venue booking and digital ticketing platform where every decision is made by cooperating software agents.**

Built for *IT4123 - Agent Based Computing (P), ICA-02*.

- **Live site:** https://eventsphere-production-1b740941.up.railway.app/events
- **Repository:** https://github.com/lakruwanipabasara/EventSphere
- **Report:** [`docs/REPORT.md`](docs/REPORT.md) 

> Payments are simulated. No real card, bank or payment gateway is used.

---

## Features

**Attendees**
- Register and sign in (passwords stored hashed with bcrypt)
- Browse events, search by keyword, filter by category, venue, date range and price
- Book and cancel tickets, with a refund calculated by a stated policy
- Join a waitlist for sold-out events, with a timed seat hold and countdown
- Show a unique QR code ticket on the phone
- Request a whole venue for private hire and get an instant decision, a price and alternatives when refused

**Admin / organiser**
- Create and edit venues and events (draft, publish, cancel)
- Approve or reject hire requests that the system escalates
- Dashboard with tickets sold, simulated revenue, check-ins, capacity and charts
- Readable log of every message exchanged between agents

**Gate staff**
- Scan tickets with the phone camera, or type the code manually
- Clear rejection reasons: already used, cancelled, wrong event, outside check-in time

---

## Multi-agent design

Agents own their goal, keep persistent state and talk **only through JSON messages** on a message bus (REQUEST, AGREE, REFUSE, PROPOSE, INFORM, FAILURE). Every message carries a sender, receiver, performative, content and conversation id, and is logged so one user action can be traced across agents.

| Agent | Goal | Key decisions |
|---|---|---|
| **VenueAgent** | Keep every venue calendar free of clashes | Accept or refuse event slots (30 min buffer); confirm, escalate or counter-propose hires; price hires with weekend (+25%) and peak (+15% from 18:00) surcharges |
| **BookingAgent** | Sell tickets fairly without overselling | Max 6 tickets per request and per user per event; timing and capacity checks; refunds (100% at 7+ days, 50% at 48 h+, otherwise 0%); full refunds when an event is cancelled |
| **TicketAgent** | Admit each ticket exactly once | Issue unique codes; admit, already used, wrong event, cancelled or outside check-in time; flags repeated scans (3+) as suspicious |
| **WaitlistAgent** | Pass freed seats to waiting customers | Choose who gets released seats; hold for a time limit; a timer sweep expires unclaimed holds and re-offers them |
| **NotificationAgent** | Tell the right person what affects them | Listens to INFORM events and turns them into user notifications |

Every decision rule is listed in [`docs/REPORT.md`](docs/REPORT.md).

**Concurrency.** Seat allocation uses a single atomic conditional database update, so if two people book the last seat together, one update succeeds and the other matches zero rows and is refused. This works the same on PostgreSQL and SQLite. The test `test_never_oversells_under_concurrency` runs 8 threads against 3 seats and asserts exactly 3 bookings.

### Advanced features implemented
1. Waitlist with timed holds, visible countdown and automatic re-offer
2. Weekend and peak-time venue hire pricing
3. Detection of suspicious repeated scans
4. HTTP inbox endpoint per agent (`/api/agents/{name}/inbox`)
5. Dashboard charts (sales and check-ins per event, check-ins over time)
6. Advanced search (keyword, category, venue, date range, maximum price)

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic |
| Database | PostgreSQL in production, SQLite for local development (SQLAlchemy) |
| Agent messaging | JSON messages on an in-app message bus, HTTP inbox via HTTPX |
| Frontend | Jinja2 templates, Bootstrap 5, vanilla JavaScript, works at 375 px |
| QR codes | `qrcode` (generation), `html5-qrcode` (browser scanning) |
| Testing | Pytest |
| Deployment | Railway (HTTPS) |

---

## Run locally

**Windows (Command Prompt)**
```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

**macOS / Linux**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/events. Interactive API docs are at http://127.0.0.1:8000/docs.

Demo data is created automatically on first start, using SQLite, so no setup is needed.

## Demo accounts

| Role | Email | Password |
|---|---|---|
| Admin | `admin@eventsphere.test` | `Admin#2026` |
| Staff | `staff@eventsphere.test` | `Staff#2026` |
| Attendee | `attendee@eventsphere.test` | `Attendee#2026` |

**Quick demo:** "Open Mic Tonight" is seeded so its check-in window is open now. Book it as the attendee, then scan the ticket as staff. "Poetry Night" has only 5 seats, so sell them out and try the waitlist.

## Environment variables

Settings are read from environment variables. `.env.example` lists the names. Never commit a real `.env`.

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL URL in production | `sqlite:///./eventsphere.db` |
| `SECRET_KEY` | Session signing key (required in production) | random on each start |
| `COOKIE_SECURE` | Set `true` on HTTPS hosts | `false` |
| `TZ_OFFSET_HOURS` | Local time offset from UTC | `5.5` |
| `AGENT_API_KEY` | Enables the agent HTTP inbox (header `X-Agent-Key`) | disabled |
| `HOLD_MINUTES` | Waitlist seat hold length | `15` |
| `SWEEP_SECONDS` | Waitlist expiry timer period | `30` |
| `SEED_DEMO` | Create demo data when the database is empty | `true` |

To set one in Windows for the current terminal: `set SECRET_KEY=any-long-random-text`

## Run the tests

```bash
pytest -v
```

26 tests cover overbooking (sequential and 8 concurrent threads), double check-in, wrong-event and cancelled scans, refund policy, every agent, waitlist expiry and re-offer, message traceability, password hashing, role checks, CSRF protection, the HTTP agent inbox and a full booking-to-scan flow.

## Send an HTTP message to an agent

```bash
curl -X POST $URL/api/agents/VenueAgent/inbox \
  -H "X-Agent-Key: $AGENT_API_KEY" -H "Content-Type: application/json" \
  -d '{"sender":"demo","receiver":"VenueAgent","performative":"REQUEST","content":{"action":"check_slot","venue_id":1,"start":"2026-12-01T10:00","end":"2026-12-01T12:00"}}'
```

## Deploy on Railway

1. Push this repository to GitHub.
2. In [Railway](https://railway.com) create a **New Project**, choose **Deploy from GitHub repo** and select the repository.
3. Add a **PostgreSQL** database to the same project.
4. In the app service open **Variables** and set `DATABASE_URL` (from the Railway PostgreSQL service), `SECRET_KEY`, `AGENT_API_KEY`, `COOKIE_SECURE=true` and `SEED_DEMO=true`.
5. Set the start command to `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`. Keep **one** worker, because the waitlist timer runs inside the app.
6. Under **Settings, Networking**, generate a public domain and open the HTTPS address.

Open the live site shortly before a demo to confirm the service is running.

## Project structure

```
eventsphere/
├── app/
│   ├── main.py            FastAPI app and scheduler start-up
│   ├── config.py          Settings and agent rule constants
│   ├── db.py, models.py   Database and tables
│   ├── bus.py             Message bus with logging
│   ├── security.py        Hashing, sessions, CSRF, role checks
│   ├── seed.py            Demo data
│   ├── agents/            Venue, Booking, Ticket, Waitlist, Notification
│   ├── routes/            auth, public, admin, staff, agents_api
│   ├── static/            CSS, JavaScript, images
│   └── templates/         Jinja2 pages
├── tests/                 Pytest suite
├── docs/                  Report (Markdown and PDF)
├── render.yaml            Optional blueprint from an earlier hosting option
├── requirements.txt
└── .env.example
```

## Security notes

- Passwords are hashed, never stored in plain text
- Role checks on every admin and staff route, including the scanner
- CSRF tokens on all forms
- No secrets in Git; configuration comes from environment variables
- Payments are simulated and no real card details are collected

## Author

E.L. Pabasara, 2021/ICT/124, IT4123 - Agent Based Computing (P)
