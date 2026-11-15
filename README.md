# EventSphere – agent-based venue booking and digital ticketing

IT4123 Agent Based Computing Practical – ICA 02. FastAPI, SQLAlchemy, Bootstrap, PostgreSQL in production.
Every booking, hire, waitlist, check-in and notification decision is made by one of five cooperating agents that
communicate only through JSON messages (REQUEST, AGREE, REFUSE, PROPOSE, INFORM, FAILURE) on a message bus.
Every message is stored with a conversation id and shown on the admin pages.

| Agent | Goal |
|---|---|
| VenueAgent | clash-free venue calendar (30 min buffer), private-hire decisions and pricing |
| BookingAgent | sell tickets without overselling, refunds |
| TicketAgent | unique unguessable codes, admit each ticket once |
| WaitlistAgent | timed seat holds, timer-driven expiry and re-offer |
| NotificationAgent | turns INFORM events into user notifications |

All decision rules are listed in the report (`docs/REPORT.md`).

## Run locally
Requires Python 3.11 or newer.
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows      (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000 (API docs at `/docs`). No configuration is needed locally: the app uses SQLite
(`eventsphere.db`, created automatically) and seeds demo data on first start.

## Demo accounts (seeded on first start)
| Role | Email | Password |
|---|---|---|
| Admin | admin@eventsphere.test | Admin#2026 |
| Staff | staff@eventsphere.test | Staff#2026 |
| Attendee | attendee@eventsphere.test | Attendee#2026 |

Public registration creates attendees only. An admin creates staff or admin accounts on the **Admin → Users** page.

## Environment variables
The app reads real environment variables (it does not load a `.env` file). `.env.example` lists the names.
| Variable | Purpose | Default |
|---|---|---|
| DATABASE_URL | PostgreSQL connection URL in production | sqlite:///./eventsphere.db |
| SECRET_KEY | signs login cookies; set a long random value in production | random on each start |
| COOKIE_SECURE | set `true` on HTTPS hosts | false |
| AGENT_API_KEY | enables the agent HTTP inbox (header `X-Agent-Key`) | disabled |
| TZ_OFFSET_HOURS | local time offset from UTC | 5.5 |
| HOLD_MINUTES / SWEEP_SECONDS | waitlist hold length / timer period | 15 / 30 |
| SEED_DEMO | create demo data when the database is empty | true |

Generate a secret: `python -c "import secrets; print(secrets.token_hex(32))"`. Secrets are never committed.

## Run the tests
```bash
pytest -v
```
26 tests: overbooking (sequential and 8 concurrent buyers), double check-in, refunds, every agent, CSRF, roles, HTTP inbox.

## Deploy (Railway)
1. Push the repository to GitHub.
2. Railway: New Project, Deploy from GitHub repo, then add a PostgreSQL database to the project.
3. In the web service Variables set `DATABASE_URL` (reference the Postgres variable), `SECRET_KEY`, `AGENT_API_KEY`, `COOKIE_SECURE=true`.
4. Settings, Networking, Generate Domain. Keep a single worker: the waitlist timer runs inside the app (see `Procfile`).

## Agent HTTP inbox (advanced feature)
```bash
curl -X POST $URL/api/agents/VenueAgent/inbox -H "X-Agent-Key: $AGENT_API_KEY" -H "Content-Type: application/json" \
 -d '{"sender":"demo","receiver":"VenueAgent","performative":"REQUEST","content":{"action":"check_slot","venue_id":1,"start":"2026-12-01T10:00","end":"2026-12-01T12:00"}}'
```
