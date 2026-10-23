"""Client side: choose service → staff → day → time → confirm (and pay), manage own bookings."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    LinkPreviewOptions,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.config import Settings
from bot.db import repo
from bot.db.models import BookingStatus, Service, Staff
from bot.services import booking as bk
from bot.services.formatting import booking_card, money, when
from bot.services.notify import notify_owners
from bot.services.slots import bookable_days, slots_for

router = Router(name="client")
router.message.filter(F.chat.type == ChatType.PRIVATE)

Clock = Callable[[], datetime]


def greeting(settings: Settings) -> str:
    return (
        f"👋 Welcome to <b>{escape(settings.business_name)}</b>!\n"
        "Book an appointment in a few taps — pick a service, a specialist and a time that suits you."
    )


@router.message(CommandStart())
async def start(message: Message, settings: Settings) -> None:
    preview = None
    if settings.welcome_image_url:
        # Shows the banner as a large image above the text, and the message stays editable
        preview = LinkPreviewOptions(url=settings.welcome_image_url, prefer_large_media=True, show_above_text=True)
    await message.answer(greeting(settings), reply_markup=kb.main_menu(), link_preview_options=preview)


async def _load(session: AsyncSession, service_id: int, staff_id: int) -> tuple[Service | None, Staff | None]:
    service = await session.get(Service, service_id)
    staff = await session.get(Staff, staff_id)
    if not (service and service.active and staff and staff.active):
        return None, None
    return service, staff


@router.callback_query(kb.MenuCb.filter(F.action == "book"))
async def choose_service(cb: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    services = await repo.active_services(session)
    if not services:
        await cb.answer("No services available right now.", show_alert=True)
        return
    await cb.message.edit_text("💈 <b>Choose a service</b>", reply_markup=kb.services_kb(services, settings.currency))
    await cb.answer()


@router.callback_query(kb.ServiceCb.filter())
async def choose_staff(cb: CallbackQuery, callback_data: kb.ServiceCb, session: AsyncSession) -> None:
    staff = await repo.active_staff(session)
    service = await session.get(Service, callback_data.service)
    if not service or not staff:
        await cb.answer("This service is not available.", show_alert=True)
        return
    await cb.message.edit_text(
        f"<b>{escape(service.name)}</b>\n👤 <b>Choose a specialist</b>",
        reply_markup=kb.staff_kb(service.id, staff),
    )
    await cb.answer()


@router.callback_query(kb.StaffCb.filter())
async def choose_day(
    cb: CallbackQuery, callback_data: kb.StaffCb, session: AsyncSession, settings: Settings, clock: Clock
) -> None:
    service, staff = await _load(session, callback_data.service, callback_data.staff)
    if not service:
        await cb.answer("Not available anymore.", show_alert=True)
        return
    now = clock()
    today = now.astimezone(settings.tz).date()
    days = [
        d
        for d in bookable_days(today, settings.booking_days_ahead, settings.work_days)
        if await slots_for(session, settings, service, staff, d, now)
    ]
    if not days:
        await cb.message.edit_text(
            f"😔 {escape(staff.name)} is fully booked for the next {settings.booking_days_ahead} days.",
            reply_markup=kb.staff_kb(service.id, await repo.active_staff(session)),
        )
        await cb.answer()
        return
    await cb.message.edit_text(
        f"<b>{escape(service.name)}</b> with {escape(staff.name)}\n🗓 <b>Choose a day</b>",
        reply_markup=kb.days_kb(service.id, staff.id, days),
    )
    await cb.answer()


@router.callback_query(kb.DayCb.filter())
async def choose_time(
    cb: CallbackQuery, callback_data: kb.DayCb, session: AsyncSession, settings: Settings, clock: Clock
) -> None:
    service, staff = await _load(session, callback_data.service, callback_data.staff)
    if not service:
        await cb.answer("Not available anymore.", show_alert=True)
        return
    day = datetime.strptime(callback_data.day, "%Y%m%d").date()
    slots = await slots_for(session, settings, service, staff, day, clock())
    if not slots:
        await cb.answer("No free time left on this day, please pick another one.", show_alert=True)
        return
    await cb.message.edit_text(
        f"<b>{escape(service.name)}</b> with {escape(staff.name)}\n"
        f"🗓 {day.strftime('%A, %d %B')}\n🕐 <b>Choose a time</b>",
        reply_markup=kb.slots_kb(service.id, staff.id, slots, settings),
    )
    await cb.answer()


@router.callback_query(kb.SlotCb.filter())
async def review_booking(
    cb: CallbackQuery, callback_data: kb.SlotCb, session: AsyncSession, settings: Settings
) -> None:
    service, staff = await _load(session, callback_data.service, callback_data.staff)
    if not service:
        await cb.answer("Not available anymore.", show_alert=True)
        return
    start = datetime.fromtimestamp(callback_data.ts, UTC)
    needs_deposit = settings.payments_enabled and service.deposit > 0

    lines = [
        "📝 <b>Check your booking</b>\n",
        f"💈 {escape(service.name)} with {escape(staff.name)}",
        f"🗓 {when(start, settings.tz)} ({service.duration_min} min)",
        f"💵 {money(service.price, settings.currency)}",
    ]
    if needs_deposit:
        lines.append(
            f"\nTo secure the slot, a deposit of <b>{money(service.deposit, settings.currency)}</b> is required. "
            f"It's deducted from the price and refundable if you cancel at least {settings.free_cancel_hours}h before."
        )
    pay_text = f"💳 Pay {money(service.deposit, settings.currency)} & book" if needs_deposit else "✅ Confirm booking"
    day = start.astimezone(settings.tz).strftime("%Y%m%d")
    await cb.message.edit_text(
        "\n".join(lines), reply_markup=kb.confirm_kb(service.id, staff.id, callback_data.ts, day, pay_text)
    )
    await cb.answer()


@router.callback_query(kb.ConfirmCb.filter())
async def create_booking(
    cb: CallbackQuery,
    callback_data: kb.ConfirmCb,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    clock: Clock,
) -> None:
    service, staff = await _load(session, callback_data.service, callback_data.staff)
    if not service:
        await cb.answer("Not available anymore.", show_alert=True)
        return
    now = clock()
    start = datetime.fromtimestamp(callback_data.ts, UTC)
    end = start + timedelta(minutes=service.duration_min)
    ttl = timedelta(minutes=settings.pending_payment_minutes)

    if start < now + timedelta(minutes=settings.min_lead_minutes) or await repo.has_conflict(
        session, staff.id, start, end, now, ttl
    ):
        await cb.answer("Sorry, this time was just taken. Please pick another one.", show_alert=True)
        return

    needs_deposit = settings.payments_enabled and service.deposit > 0
    user = cb.from_user
    booking = await repo.create_booking(
        session,
        user.id,
        user.full_name,
        service,
        staff,
        start,
        BookingStatus.PENDING if needs_deposit else BookingStatus.CONFIRMED,
        now,
    )

    if not needs_deposit:
        bk.confirm(booking, now)
        await cb.message.edit_text(
            f"✅ <b>You're booked!</b>\n\n{booking_card(booking, settings.tz, settings.currency)}\n\n"
            "I'll remind you before the visit.",
            reply_markup=kb.booking_actions_kb(booking.id),
        )
        await cb.answer()
        await bk.announce_new(bot, booking, settings)
        return

    await cb.message.edit_text(
        f"⏳ The slot is held for you for {settings.pending_payment_minutes} minutes.\n"
        "Pay the deposit below to confirm your booking."
    )
    await bot.send_invoice(
        chat_id=user.id,
        title=f"Deposit · {service.name}",
        description=f"{staff.name} · {when(start, settings.tz)}. The deposit is deducted from the final price.",
        payload=f"booking:{booking.id}",
        provider_token=settings.payment_provider_token.get_secret_value(),
        currency=settings.currency,
        prices=[LabeledPrice(label="Deposit", amount=service.deposit)],
        photo_url=settings.invoice_photo_url or None,
        photo_width=1200 if settings.invoice_photo_url else None,
        photo_height=630 if settings.invoice_photo_url else None,
    )
    await cb.answer()


@router.callback_query(kb.MenuCb.filter(F.action == "my"))
async def my_bookings(cb: CallbackQuery, session: AsyncSession, settings: Settings, clock: Clock) -> None:
    bookings = await repo.user_upcoming(session, cb.from_user.id, clock())
    if not bookings:
        await cb.message.edit_text("You have no upcoming bookings.", reply_markup=kb.main_menu())
        await cb.answer()
        return
    text = ["🗂 <b>Your upcoming bookings</b>\n"]
    builder = InlineKeyboardBuilder()
    for b in bookings:
        text.append(booking_card(b, settings.tz, settings.currency) + "\n")
        builder.button(text=f"✖️ Cancel · {when(b.start_at, settings.tz)}", callback_data=kb.CancelCb(booking=b.id))
    builder.button(text="📅 Book another", callback_data=kb.MenuCb(action="book"))
    builder.adjust(1)
    await cb.message.edit_text("\n".join(text), reply_markup=builder.as_markup())
    await cb.answer()


@router.callback_query(kb.CancelCb.filter())
async def cancel_booking(
    cb: CallbackQuery,
    callback_data: kb.CancelCb,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    clock: Clock,
) -> None:
    booking = await repo.get_booking(session, callback_data.booking)
    now = clock()
    if (
        booking is None
        or booking.user_id != cb.from_user.id
        or booking.status != BookingStatus.CONFIRMED
        or booking.start_at <= now
    ):
        await cb.answer("This booking can't be cancelled.", show_alert=True)
        return

    if not callback_data.sure:
        if not booking.deposit:
            policy = ""
        elif bk.refundable(booking, now, settings):
            policy = "\nYour deposit will be refunded."
        else:
            policy = (
                f"\n⚠️ It's less than {settings.free_cancel_hours}h before the visit, so the deposit is non-refundable."
            )
        await cb.message.edit_text(
            f"Cancel this booking?\n\n{booking_card(booking, settings.tz, settings.currency)}\n{policy}",
            reply_markup=kb.cancel_confirm_kb(booking.id),
        )
        await cb.answer()
        return

    booking.status = BookingStatus.CANCELLED
    await cb.message.edit_text("❌ Your booking is cancelled.", reply_markup=kb.main_menu())
    await cb.answer()
    await bk.announce_cancel(bot, booking, settings, now, by_client=True)


@router.callback_query(kb.AttendCb.filter())
async def attend(cb: CallbackQuery) -> None:
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Great, see you soon! 👋")


@router.callback_query(kb.RateCb.filter())
async def rate(
    cb: CallbackQuery, callback_data: kb.RateCb, bot: Bot, session: AsyncSession, settings: Settings
) -> None:
    booking = await repo.get_booking(session, callback_data.booking)
    if booking is None or booking.user_id != cb.from_user.id or booking.rating is not None:
        await cb.answer()
        return
    score = max(1, min(5, callback_data.score))
    booking.rating = score

    if score >= 4 and settings.review_url:
        await cb.message.edit_text(
            "🙏 Thank you! Would you mind sharing it in a public review? It helps us a lot.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⭐ Leave a review", url=settings.review_url)]]
            ),
        )
    else:
        await cb.message.edit_text("🙏 Thank you for your feedback!")
    await cb.answer()

    if score <= 3:
        await notify_owners(
            bot,
            settings.owner_ids,
            f"⚠️ <b>Low rating: {score}/5</b> from {escape(booking.user_name)}\n"
            f"{escape(booking.service.name)} with {escape(booking.staff.name)}"
            f" · {when(booking.start_at, settings.tz)}\n"
            f"Consider reaching out: <a href='tg://user?id={booking.user_id}'>open chat</a>",
        )
