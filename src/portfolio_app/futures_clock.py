"""Exchange business dates and settlement reference times, never retrieval times."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

EXCHANGE_ZONE = ZoneInfo('Europe/Berlin')


def utc_now():
    return datetime.now(timezone.utc)


def instant(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timestamp requires a timezone')
    return stamp


def business_date(now):
    return now.astimezone(EXCHANGE_ZONE).date()


def settlement_reference(day):
    # The curated Eurex equity-index products specify 17:30 local reference.
    # This is a comparison/polling boundary, NOT the source publication time.
    return datetime.combine(day, time(17, 30), EXCHANGE_ZONE)
