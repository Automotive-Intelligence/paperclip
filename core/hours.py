"""Business hours enforcement for all outbound messages.

No messages sent outside the defined send windows.
Agents can still ENROLL and CHECK FOR HOT LEADS anytime —
but actual message delivery respects the schedule.
"""

from datetime import datetime
import pytz

CST = pytz.timezone("America/Chicago")

# Day name mapping
DAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def is_within_send_window(schedule: dict, tolerance_minutes: int = 120) -> bool:
    """Check if current time is within the send window for a vertical.

    schedule: {"day": "tuesday", "hour": 18, "minute": 0}
    tolerance_minutes: how many minutes after the scheduled time we still allow sending (default 2hr)

    Returns True if it's the right day and within the time window.
    """
    now = datetime.now(CST)
    target_day = DAY_MAP.get(schedule.get("day", "").lower())
    target_hour = schedule.get("hour", 9)
    target_minute = schedule.get("minute", 0)

    if target_day is None:
        return False

    # Must be the right day of the week
    if now.weekday() != target_day:
        return False

    # Must be within the time window
    target_minutes = target_hour * 60 + target_minute
    current_minutes = now.hour * 60 + now.minute

    return target_minutes <= current_minutes <= (target_minutes + tolerance_minutes)


def is_business_hours() -> bool:
    """General business hours check: Mon-Fri 8am-8pm CST.
    Used for rivers without a specific per-vertical schedule.
    """
    now = datetime.now(CST)

    # No weekends
    if now.weekday() >= 5:
        return False

    # 8am - 8pm CST
    return 8 <= now.hour < 20


def is_email_hours() -> bool:
    """Email-specific hours: Mon-Fri 7am-9pm CST.
    Slightly wider than SMS since email is less intrusive.
    """
    now = datetime.now(CST)

    if now.weekday() >= 5:
        return False

    return 7 <= now.hour < 21


def get_current_cst() -> datetime:
    """Get current time in CST."""
    return datetime.now(CST)
