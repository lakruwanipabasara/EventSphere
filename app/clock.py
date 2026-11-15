"""Single source of 'now' (naive local time) so tests can move time."""
from datetime import datetime, timedelta, timezone

from app import config


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=config.TZ_OFFSET_HOURS)
