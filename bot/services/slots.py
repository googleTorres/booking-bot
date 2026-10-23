"""Free-slot calculation. Pure functions — all datetimes are timezone-aware."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db import repo
from bot.db.models import Service, Staff


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def free_slots(
    day: date,
    tz: ZoneInfo,
    work_start: time,
    work_end: time,
    step_min: int,
    duration_min: int,
    busy: list[tuple[datetime, datetime]],
    now: datetime,
    min_lead_min: int = 0,
) -> list[datetime]:
    """Start times (in UTC) on `day` (business-local date) where a service of `duration_min` fits
    inside working hours, doesn't overlap `busy` intervals and starts at least `min_lead_min` from now."""
    duration = timedelta(minutes=duration_min)
    earliest = now + timedelta(minutes=min_lead_min)
    cursor = datetime.combine(day, work_start, tzinfo=tz)
    closing = datetime.combine(day, work_end, tzinfo=tz)

    slots = []
    while cursor + duration <= closing:
        start = cursor.astimezone(UTC)
        end = start + duration
        if start >= earliest and not any(overlaps(start, end, b_start, b_end) for b_start, b_end in busy):
            slots.append(start)
        cursor += timedelta(minutes=step_min)
    return slots


def bookable_days(today: date, days_ahead: int, work_days: list[int]) -> list[date]:
    return [d for d in (today + timedelta(days=i) for i in range(days_ahead)) if d.weekday() in work_days]


def day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


async def slots_for(
    session: AsyncSession, settings: Settings, service: Service, staff: Staff, day: date, now: datetime
) -> list[datetime]:
    if day.weekday() not in settings.work_days:
        return []
    day_start, day_end = day_bounds_utc(day, settings.tz)
    busy = await repo.busy_intervals(
        session, staff.id, day_start, day_end, now, timedelta(minutes=settings.pending_payment_minutes)
    )
    return free_slots(
        day,
        settings.tz,
        settings.work_start,
        settings.work_end,
        settings.slot_step_minutes,
        service.duration_min,
        busy,
        now,
        settings.min_lead_minutes,
    )
