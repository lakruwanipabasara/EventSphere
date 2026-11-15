"""All settings and agent rule constants. Secrets come from environment variables."""
import os
import secrets


def _bool(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


def _db_url():
    url = os.getenv("DATABASE_URL", "sqlite:///./eventsphere.db")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _db_url()
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
COOKIE_SECURE = _bool("COOKIE_SECURE", False)
TZ_OFFSET_HOURS = float(os.getenv("TZ_OFFSET_HOURS", "5.5"))  # Sri Lanka = UTC+5:30
AGENT_API_KEY = os.getenv("AGENT_API_KEY", "")  # empty = HTTP agent inbox disabled
ENABLE_SCHEDULER = _bool("ENABLE_SCHEDULER", True)
SEED_DEMO = _bool("SEED_DEMO", True)
DEMO_ADMIN_PASSWORD = os.getenv("DEMO_ADMIN_PASSWORD", "Admin#2026")
DEMO_STAFF_PASSWORD = os.getenv("DEMO_STAFF_PASSWORD", "Staff#2026")
DEMO_ATTENDEE_PASSWORD = os.getenv("DEMO_ATTENDEE_PASSWORD", "Attendee#2026")

# ---- BookingAgent rules ----
MAX_TICKETS_PER_REQUEST = 6
MAX_TICKETS_PER_USER_EVENT = 6
REFUND_FULL_HOURS = 168   # >= 7 days before start -> 100 %
REFUND_HALF_HOURS = 48    # >= 48 h -> 50 %, otherwise 0 %

# ---- VenueAgent rules ----
BUFFER_MINUTES = 30
HIRE_OPEN_HOUR = 8
HIRE_CLOSE_HOUR = 23
HIRE_MIN_NOTICE_HOURS = 24
HIRE_MIN_HOURS = 1
HIRE_MAX_HOURS = 12
AUTO_APPROVE_MAX_HOURS = 6
AUTO_APPROVE_MAX_PRICE = 150000
AUTO_APPROVE_MAX_FILL = 0.8
WEEKEND_SURCHARGE = 0.25
PEAK_SURCHARGE = 0.15
PEAK_FROM_HOUR = 18

# ---- TicketAgent rules ----
CHECKIN_OPEN_HOURS = 2
SUSPICIOUS_SCAN_THRESHOLD = 3

# ---- WaitlistAgent rules ----
HOLD_MINUTES = int(os.getenv("HOLD_MINUTES", "15"))
SWEEP_SECONDS = int(os.getenv("SWEEP_SECONDS", "30"))
