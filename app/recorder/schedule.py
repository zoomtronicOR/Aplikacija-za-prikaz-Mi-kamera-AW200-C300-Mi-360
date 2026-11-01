from datetime import datetime, time


def in_window(now: datetime, start: time | None, end: time | None, days: str) -> bool:
    """True if local time `now` is inside the daily window [start, end) on one of `days` (Monday=0).

    A window whose end is before its start crosses midnight and belongs to the day it starts on.
    Equal start and end (e.g. 00:00–00:00) means the whole day.
    """
    if start is None or end is None:
        return False
    t, wd = now.time(), now.weekday()
    if start == end:
        return str(wd) in days
    if start <= end:
        return str(wd) in days and start <= t < end
    return (str(wd) in days and t >= start) or (str((wd - 1) % 7) in days and t < end)
