"""Time in Japan Standard Time, the calendar every notification is written in."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

#: Hour the next-morning outlook is projected to.
MORNING = time(7, 0)


def now() -> datetime:
    return datetime.now(UTC)


def to_jst(moment: datetime) -> datetime:
    return moment.astimezone(JST)


def next_morning(moment: datetime) -> datetime:
    """The first 07:00 JST strictly after ``moment``, in UTC."""
    local = to_jst(moment)
    morning = datetime.combine(local.date(), MORNING, tzinfo=JST)
    if morning <= local:
        morning += timedelta(days=1)
    return morning.astimezone(UTC)


def previous_iso_week(moment: datetime) -> tuple[int, int]:
    """ISO (year, week) of the week before the one containing ``moment``."""
    local = to_jst(moment)
    year, week, _ = (local.date() - timedelta(days=7)).isocalendar()
    return year, week


def iso_week_bounds(iso_year: int, iso_week: int) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of an ISO week as UTC timestamps."""
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    start = datetime.combine(monday, time(), tzinfo=JST)
    return start.astimezone(UTC), (start + timedelta(days=7)).astimezone(UTC)


def iso_week_key(iso_year: int, iso_week: int) -> str:
    return f"{iso_year}-W{iso_week:02d}"


def jst_dates(start: datetime, end: datetime) -> list[date]:
    """Every JST calendar date touched by the half-open interval [start, end)."""
    first, last = to_jst(start).date(), to_jst(end - timedelta(microseconds=1)).date()
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
