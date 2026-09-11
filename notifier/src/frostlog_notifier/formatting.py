"""How numbers and times are written in the messages (Japanese, JST, °C)."""

from datetime import datetime

from frostlog_notifier.clock import to_jst

MISSING = "—"

BATTERY_STATE = {
    "idle": "待機",
    "charging": "充電中",
    "discharging": "放電中",
    "full": "満充電",
    "absent": "バッテリーなし",
}


def stamp(moment: datetime) -> str:
    """Month, day and time in JST, e.g. ``09-11 21:03``."""
    return to_jst(moment).strftime("%m-%d %H:%M")


def full_stamp(moment: datetime) -> str:
    """Date and time in JST, e.g. ``2026-09-11 21:03 JST``."""
    return to_jst(moment).strftime("%Y-%m-%d %H:%M JST")


def _quantity(value: float | None, digits: int, unit: str = "") -> str:
    return MISSING if value is None else f"{value:.{digits}f}{unit}"


def number(value: float | None, digits: int = 1) -> str:
    return _quantity(value, digits)


def celsius(value: float | None, digits: int = 1) -> str:
    return _quantity(value, digits, " °C")


def percent(value: float | None, digits: int = 0) -> str:
    return _quantity(value, digits, " %")


def watt_hours(value: float | None) -> str:
    return _quantity(value, 1, " Wh")


def hours(value: float | None) -> str:
    return _quantity(value, 1, " h")


def duration(seconds: float | None) -> str:
    """A span written as minutes, or as hours and minutes past an hour."""
    if seconds is None:
        return MISSING
    whole_minutes = round(seconds / 60)
    if whole_minutes < 60:
        return f"{whole_minutes} 分"
    return f"{whole_minutes // 60} 時間 {whole_minutes % 60} 分"


def battery_state(value: str) -> str:
    return BATTERY_STATE.get(value, value)
