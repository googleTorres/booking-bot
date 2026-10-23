"""Background jobs: expire unpaid holds, send reminders, ask for ratings."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot import keyboards as kb
from bot.config import Settings
from bot.db.models import Booking, BookingStatus
from bot.services.booking import REMIND_2H, REMIND_24H
from bot.services.formatting import booking_card, hhmm
from bot.services.notify import send_safe

log = logging.getLogger(__name__)


@dataclass
class JobStats:
    expired: int = 0
    reminded_24h: int = 0
    reminded_2h: int = 0
    review_asked: int = 0


async def run_due(session: AsyncSession, bot: Bot, settings: Settings, now: datetime) -> JobStats:
    stats = JobStats()

    # 1. Unpaid holds free their slot
    stale = await session.scalars(
        select(Booking).where(
            Booking.status == BookingStatus.PENDING,
            Booking.created_at <= now - timedelta(minutes=settings.pending_payment_minutes),
        )
    )
    for b in stale:
        b.status = BookingStatus.EXPIRED
        stats.expired += 1

    # 2. Reminders
    upcoming = await session.scalars(
        select(Booking).where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.start_at > now,
            Booking.start_at <= now + REMIND_24H,
            (Booking.reminded_24h.is_(False)) | (Booking.reminded_2h.is_(False)),
        )
    )
    for b in upcoming:
        if not b.reminded_2h and b.start_at - now <= REMIND_2H:
            b.reminded_2h = b.reminded_24h = True
            await send_safe(
                bot,
                b.user_id,
                f"⏰ See you at <b>{hhmm(b.start_at, settings.tz)}</b>!\n\n"
                f"{booking_card(b, settings.tz, settings.currency)}",
            )
            stats.reminded_2h += 1
        elif not b.reminded_24h:
            b.reminded_24h = True
            await send_safe(
                bot,
                b.user_id,
                f"🔔 <b>Reminder: your appointment is tomorrow</b>\n\n"
                f"{booking_card(b, settings.tz, settings.currency)}\n\nPlease confirm you're coming:",
                reply_markup=kb.booking_actions_kb(b.id, with_attend=True),
            )
            stats.reminded_24h += 1

    # 3. After the visit: mark completed and ask for a rating
    finished = await session.scalars(
        select(Booking).where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.end_at <= now - timedelta(minutes=settings.review_delay_minutes),
            Booking.review_asked.is_(False),
        )
    )
    for b in finished:
        b.status = BookingStatus.COMPLETED
        b.review_asked = True
        await send_safe(
            bot,
            b.user_id,
            f"Thanks for visiting <b>{escape(settings.business_name)}</b>! "
            f"How was your {escape(b.service.name)} with {escape(b.staff.name)}?",
            reply_markup=kb.rating_kb(b.id),
        )
        stats.review_asked += 1

    return stats


async def scheduler_loop(
    bot: Bot, sessionmaker: async_sessionmaker, settings: Settings, clock: Callable[[], datetime], interval: float = 60
) -> None:
    while True:
        try:
            async with sessionmaker() as session:
                stats = await run_due(session, bot, settings, clock())
                await session.commit()
            if any(vars(stats).values()):
                log.info("Scheduler: %s", stats)
        except Exception:
            log.exception("Scheduler iteration failed")
        await asyncio.sleep(interval)
