from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from bot.db.models import Booking

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def money(amount: int, currency: str) -> str:
    value = f"{amount / 100:,.2f}".removesuffix(".00")
    symbol = SYMBOLS.get(currency.upper())
    return f"{symbol}{value}" if symbol else f"{value} {currency.upper()}"


def when(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%a, %d %b · %H:%M")


def hhmm(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def booking_card(b: Booking, tz: ZoneInfo, currency: str) -> str:
    lines = [
        f"💈 <b>{escape(b.service.name)}</b> with {escape(b.staff.name)}",
        f"🗓 {when(b.start_at, tz)} ({b.service.duration_min} min)",
        f"💵 {money(b.service.price, currency)}",
    ]
    if b.deposit:
        lines[-1] += f" · deposit {money(b.deposit, currency)} paid"
    return "\n".join(lines)
