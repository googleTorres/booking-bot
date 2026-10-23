"""Booking state transitions shared by handlers, payments and the scheduler."""

from datetime import datetime, timedelta
from html import escape

from aiogram import Bot

from bot.config import Settings
from bot.db.models import Booking, BookingStatus
from bot.services.formatting import booking_card, money, when
from bot.services.notify import notify_owners

REMIND_24H = timedelta(hours=24)
REMIND_2H = timedelta(hours=2)


def confirm(booking: Booking, now: datetime, charge_id: str | None = None) -> None:
    booking.status = BookingStatus.CONFIRMED
    booking.charge_id = charge_id
    # Booked at short notice: don't send reminders that are already "late"
    until = booking.start_at - now
    booking.reminded_24h = until <= REMIND_24H
    booking.reminded_2h = until <= REMIND_2H


def refundable(booking: Booking, now: datetime, settings: Settings) -> bool:
    return booking.deposit > 0 and booking.start_at - now >= timedelta(hours=settings.free_cancel_hours)


async def announce_new(bot: Bot, booking: Booking, settings: Settings) -> None:
    await notify_owners(
        bot,
        settings.owner_ids,
        f"🆕 <b>New booking #{booking.id}</b>\n"
        f"{booking_card(booking, settings.tz, settings.currency)}\n"
        f"👤 {escape(booking.user_name)} (<a href='tg://user?id={booking.user_id}'>open chat</a>)",
    )


async def announce_cancel(bot: Bot, booking: Booking, settings: Settings, now: datetime, by_client: bool) -> None:
    who = "client" if by_client else "you"
    text = (
        f"❌ <b>Booking #{booking.id} cancelled by {who}</b>\n"
        f"{escape(booking.service.name)} · {when(booking.start_at, settings.tz)} · {escape(booking.user_name)}"
    )
    if booking.deposit:
        if refundable(booking, now, settings) or not by_client:
            text += f"\n💸 Please refund the deposit of {money(booking.deposit, settings.currency)}"
            if booking.charge_id:
                text += f" (payment ID: <code>{escape(booking.charge_id)}</code>)"
        else:
            text += "\nℹ️ Late cancellation: deposit is non-refundable."
    await notify_owners(bot, settings.owner_ids, text)
