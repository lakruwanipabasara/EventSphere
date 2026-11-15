---
title: "EventSphere – IT4123 ICA 02 Report"
---

# 1. Submission details

| Item | Value |
|---|---|
| Live URL (HTTPS) | **<paste your Render URL here>** |
| GitHub repository | **<paste your repository URL here>** (lecturer added as collaborator if private) |
| Student | <name / index number> |

## Demo accounts

| Role | Email | Password |
|---|---|---|
| Admin | admin@eventsphere.test | Admin#2026 |
| Staff | staff@eventsphere.test | Staff#2026 |
| Attendee | attendee@eventsphere.test | Attendee#2026 |

# 2. Architecture

EventSphere is a FastAPI application with server-rendered Bootstrap pages (works at 375 px), SQLAlchemy models, PostgreSQL in production and SQLite locally.

The web routes contain no business decisions. A route turns a user action into a **REQUEST message** and sends it to an agent through the `MessageBus`. The agent decides and answers with AGREE, REFUSE, PROPOSE, INFORM or FAILURE. The route only shows the answer.

* **Messages.** `Message` is a Pydantic model: `sender`, `receiver`, `performative`, `content` (JSON), `conversation_id`, `timestamp`. Agents never import or call each other; they only call `bus.request(...)` or broadcast an INFORM.
* **Traceability.** The bus writes every message to the `message_log` table with its conversation id. All messages caused by one user action share that id, so the admin page `/admin/messages?conv=<id>` shows the whole chain across agents. Ticket codes are redacted in the log.
* **State.** Each agent keeps persistent beliefs in the `agent_state` table (counters such as `tickets_sold`, `holds_expired`, `duplicate_scans`). They survive restarts. Per-ticket scan counts and hold expiry times are stored on their rows.
* **Reactive behaviour.** Broadcast INFORM events call `on_<event>()` handlers: BookingAgent reacts to `event_cancelled` (full refunds), WaitlistAgent reacts to `tickets_released` (offers seats), NotificationAgent reacts to nearly every event, VenueAgent reacts to `event_cancelled` (frees the slot).
* **Proactive behaviour.** A background timer calls `WaitlistAgent.tick()` every 30 s. With no user action it expires overdue holds, asks BookingAgent to release the seats and re-offers them to the next person in line.
* **HTTP inboxes (advanced).** `POST /api/agents/{name}/inbox` accepts a JSON message (header `X-Agent-Key`) and returns the agent's reply. Documented in `/docs`.

```
Browser → FastAPI route → MessageBus ──► VenueAgent
                              │  ▲  ───► BookingAgent ──► TicketAgent
                              │  │  ───► WaitlistAgent ──► BookingAgent
                              ▼  │  ───► NotificationAgent (listens to all INFORM)
                         message_log (conversation id)
```

Example conversation, one booking: `user → BookingAgent REQUEST book` → `BookingAgent → TicketAgent REQUEST issue_tickets` → `TicketAgent → BookingAgent AGREE` → `BookingAgent → * INFORM booking_confirmed` (NotificationAgent writes the notification) → `BookingAgent → user AGREE`.

# 3. Agent decision rules

## VenueAgent – goal: keep every venue's calendar free of clashes
| Rule | Decision |
|---|---|
| V1 | REFUSE if the venue does not exist |
| V2 | REFUSE if end is not after start (hire: 30-minute steps, 1–12 hours) |
| V3 | REFUSE an event if total tickets exceed venue capacity |
| V4 | REFUSE a slot that overlaps any non-cancelled event or confirmed/pending hire, **including a 30-minute turnaround buffer**; the REFUSE carries the clashing activities and free alternative slots |
| H1 | REFUSE hire if the venue is not open for private hire |
| H3 | REFUSE hire with less than 24 hours' notice |
| H4 | REFUSE hire outside 08:00–23:00 or spanning days |
| H5 | If guests exceed capacity, PROPOSE other hire venues that fit and are free |
| H6 | If the slot clashes, PROPOSE up to 3 free times at the same venue (nearest first, 2 h apart) and up to 3 other free venues at the same time; REFUSE only if nothing is free |
| H7 | **Auto-confirm** (AGREE) routine requests; **escalate** (INFORM, status pending) if longer than 6 h, price above Rs 150,000, or guests above 80 % of capacity |
| H8 | Price = hourly rate × hours, +25 % on Saturday/Sunday, +15 % for half-hours starting at or after 18:00 (advanced: peak pricing) |
| H9 | Admin approval re-checks the calendar and REFUSES if the slot was taken meanwhile |

## BookingAgent – goal: sell tickets fairly without overselling
| Rule | Decision |
|---|---|
| B1 | REFUSE quantity below 1 or above 6 per request |
| B2 | REFUSE if the event is not published |
| B3 | REFUSE once the event has started |
| B4 | REFUSE if the person would hold more than 6 tickets for one event |
| B5 | REFUSE if fewer seats remain than requested (tells how many are left and that a waitlist exists) |
| B6 | REFUSE cancellation after event start, for other people's bookings, or if a ticket was already used |
| B7 | Refund: **100 %** if 7+ days before start, **50 %** from 48 h to 7 days, **0 %** under 48 h |
| B8 | When an event is cancelled: refund every booking 100 % and cancel its tickets |
| B9 | Grant or refuse seat holds for the WaitlistAgent; convert a hold into a booking when claimed |
| B10 | If TicketAgent fails to issue tickets, undo the seat reservation and delete the booking (FAILURE) |

## TicketAgent – goal: admit each ticket exactly once
| Rule | Decision |
|---|---|
| T1 | Issue one unique code per ticket using `secrets.token_urlsafe(16)` (128 bits, unguessable) |
| T2 | REFUSE `unknown_ticket` |
| T3 | REFUSE `wrong_event` (names the event the ticket is for) |
| T4 | REFUSE `ticket_cancelled` |
| T5 | REFUSE `already_used`; counts the scan; at the 3rd scan flags the ticket as suspicious (possible shared screenshot) and broadcasts INFORM to admins |
| T6 | REFUSE `outside_checkin_time`: check-in opens 2 hours before start and closes at event end; REFUSE `event_cancelled` |
| T7 | Otherwise mark used, record time and staff member, AGREE "Admit one" |

## WaitlistAgent – goal: pass freed seats to waiting customers
| Rule | Decision |
|---|---|
| W1–W4 | REFUSE joining if quantity is not 1–6, the event is closed, seats are still available, or the person is already waiting |
| W5 | When seats are released, offer to waiting entries in **first-come-first-served** order; an entry too large for the free seats is skipped |
| W6 | A granted offer holds the seats for **15 minutes**; nobody else can buy them |
| W7 | REFUSE a claim after the hold expired |
| W8 | **Timer (every 30 s):** expire overdue holds, release the seats, notify, and re-offer to the next entries |
| W9 | When an event is cancelled, close all waiting and held entries |

## NotificationAgent – goal: tell the right person what affects them
Listens to every INFORM and writes a notification for the affected user only: booking confirmed, booking cancelled with refund, event-cancel refund, waitlist offer, hold expired, hire decision. Admins receive hire requests needing review and suspicious-scan alerts.

# 4. Concurrency (no overselling)

Seat allocation is one **conditional SQL UPDATE**:

`UPDATE ticket_categories SET sold = sold + :q WHERE id = :id AND sold + held + :q <= quantity`

The database applies it atomically. If two people book the last seat together, the first update succeeds and the second matches zero rows (`rowcount == 0`) and is refused. No "read, check, write" gap exists, so it behaves the same on PostgreSQL and SQLite and needs no application locks. Seat holds, releases and counters use the same pattern. If a later step fails, the booking is compensated (counters reversed). The test `test_never_oversells_under_concurrency` starts 8 threads against 3 seats and asserts exactly 3 bookings.

# 5. Security

* Passwords hashed with bcrypt, never stored in plain text.
* Roles checked by a dependency on every admin route (`require_role("admin")`) and on the scanner (staff/admin).
* CSRF: a per-session token is required on every POST (form field or `X-CSRF-Token` header).
* Secrets only in environment variables; `.env` is git-ignored.
* Simulated payments only: a payment reference `SIM-…` is generated, no card data is collected.
* Ticket QR images are served only to the ticket owner, staff or admin.

# 6. Advanced features implemented

1. **Waitlist with timed holds**, visible countdown on the event page, automatic re-offer by the timer.
2. **Weekend and peak pricing** decided by VenueAgent (rule H8).
3. **Suspicious repeated scan detection** (rule T5).
4. **HTTP inbox per agent** (`/api/agents/{name}/inbox`).
5. **Dashboard charts** (sales and check-ins per event, check-ins over time).
6. **Advanced search**: keyword, category, venue, date range, maximum price.

# 7. Testing

`pytest -v` runs 20+ tests: overbooking (sequential and concurrent), double check-in, wrong event / cancelled / early scan, refund policy, event-cancel refunds, venue buffer and capacity, hire alternatives, escalation and weekend pricing, waitlist hold and claim, timer expiry and re-offer, notifications reach the right user, conversation-id traceability, persistent beliefs, password hashing, role checks, CSRF rejection, HTTP inbox key, and a full HTTP booking-to-scan flow.

# 8. Limitations

Images are stored as URLs. Agents run in one process (a synchronous in-process bus), so the app must run with one worker; the HTTP inbox shows how agents could be split into separate services. Free-tier Render sleeps when idle, and the free PostgreSQL instance expires after a time limit.
