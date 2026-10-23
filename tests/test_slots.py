from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.handlers.owner import parse_amount
from bot.handlers.payments import parse_payload
from bot.services.formatting import money
from bot.services.slots import bookable_days, free_slots

LON = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")
DAY = date(2026, 11, 3)  # Tuesday
EARLY = datetime(2026, 11, 1, tzinfo=UTC)


def utc(h, m=0, d=DAY):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def slots(**kw):
    params = dict(
        day=DAY, tz=LON, work_start=time(10), work_end=time(12), step_min=30, duration_min=45, busy=[], now=EARLY
    )
    params.update(kw)
    return free_slots(**params)


def test_slots_fit_inside_working_hours():
    # 45-min service, 10:00–12:00, step 30 → 10:00, 10:30, 11:00 (11:30 would end after closing)
    assert slots() == [utc(10), utc(10, 30), utc(11)]


def test_busy_interval_blocks_overlapping_starts():
    busy = [(utc(10, 30), utc(11, 0))]
    # 10:00 overlaps (ends 10:45), 10:30 overlaps, 11:00 is free
    assert slots(busy=busy) == [utc(11)]


def test_touching_intervals_do_not_overlap():
    busy = [(utc(9), utc(10))]
    assert utc(10) in slots(busy=busy)


def test_lead_time_hides_too_soon_slots():
    now = utc(9, 45)
    assert slots(now=now, min_lead_min=60) == [utc(11)]


def test_timezone_is_respected():
    # 10:00 in New York = 15:00 UTC in November
    assert slots(tz=NY, work_end=time(11), duration_min=60) == [utc(15)]


def test_bookable_days_skip_days_off():
    monday = date(2026, 11, 2)
    days = bookable_days(monday, 7, work_days=[0, 1, 2, 3, 4])
    assert [d.weekday() for d in days] == [0, 1, 2, 3, 4]
    assert days[0] == monday and days[-1] == monday + timedelta(days=4)


def test_money_and_parsers():
    assert money(3000, "USD") == "$30"
    assert money(2990, "EUR") == "€29.90"
    assert money(150000, "AED") == "1,500 AED"
    assert parse_amount("29,90") == 2990
    assert parse_amount("$30") == 3000
    assert parse_amount("abc") is None
    assert parse_payload("booking:42") == 42
    assert parse_payload("other:42") is None
