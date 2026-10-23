"""Telegram Payments: validate before charging, confirm the booking after a successful payment."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.config import Settings
from bot.db import repo
from bot.db.models import Booking, BookingStatus
from bot.services import booking as bk
from bot.services.formatting import booking_card, money
from bot.services.notify import notify_owners

log = logging.getLogger(__name__)
router = Router(name="payments")

# Small grace period: Telegram may take a moment between checkout and payment confirmation
GRACE = timedelta(minutes=2)


def parse_payload(payload: str) -> int | None:
    prefix, _, value = payload.partition(":")
    return int(value) if prefix == "booking" and value.isdigit() else None


async def _booking(session: AsyncSession, payload: str) -> Booking | None:
    booking_id = parse_payload(payload)
    return await repo.get_booking(session, booking_id) if booking_id else None


@router.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery, session: AsyncSession, settings: Settings, clock: Callable[[], datetime]
) -> None:
    booking = await _booking(session, query.invoice_payload)
    now = clock()
    ttl = timedelta(minutes=settings.pending_payment_minutes)

    error = None
    if booking is None or booking.user_id != query.from_user.id:
        error = "Booking not found."
    elif booking.status != BookingStatus.PENDING or booking.created_at < now - ttl - GRACE:
        error = "This booking has expired. Please book again — it only takes a few taps."
    elif query.total_amount != booking.deposit or query.currency != settings.currency:
        error = "The amount has changed. Please book again."
    elif await repo.has_conflict(session, booking.staff_id, booking.start_at, booking.end_at, now, ttl, booking.id):
        error = "Sorry, this time was just taken. Please pick another one."

    if error:
        await query.answer(ok=False, error_message=error)
    else:
        await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(
    message: Message, bot: Bot, session: AsyncSession, settings: Settings, clock: Callable[[], datetime]
) -> None:
    payment = message.successful_payment
    booking = await _booking(session, payment.invoice_payload)
    charge_id = payment.provider_payment_charge_id or payment.telegram_payment_charge_id
    now = clock()
    ttl = timedelta(minutes=settings.pending_payment_minutes)

    usable = (
        booking is not None
        and booking.status in (BookingStatus.PENDING, BookingStatus.EXPIRED)
        # an expired hold may have been taken by someone else in the meantime
        and not await repo.has_conflict(
            session, booking.staff_id, booking.start_at, booking.end_at, now, ttl, booking.id
        )
    )
    if not usable:
        # Money was taken but there's nothing to confirm — make sure a human handles it
        log.error("Payment for unknown/inactive booking: %s", payment.invoice_payload)
        await notify_owners(
            bot,
            settings.owner_ids,
            f"⚠️ Payment received for an inactive booking ({escape(payment.invoice_payload)}). "
            f"Please refund {money(payment.total_amount, payment.currency)}, "
            f"payment ID: <code>{escape(charge_id)}</code>",
        )
        await message.answer("We received your payment, but the booking is no longer active. We'll refund you shortly.")
        return

    bk.confirm(booking, now, charge_id)
    await message.answer(
        f"✅ <b>Payment received — you're booked!</b>\n\n{booking_card(booking, settings.tz, settings.currency)}\n\n"
        "I'll remind you before the visit.",
        reply_markup=kb.booking_actions_kb(booking.id),
    )
    await bk.announce_new(bot, booking, settings)
