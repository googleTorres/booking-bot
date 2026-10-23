"""Owner panel inside the bot: schedule, cancellations, services and staff management."""

from collections.abc import Callable
from datetime import datetime, timedelta
from html import escape
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db import repo
from bot.db.models import BookingStatus, Service, Staff
from bot.services import booking as bk
from bot.services.formatting import hhmm, money, when
from bot.services.notify import send_safe
from bot.services.slots import day_bounds_utc

Clock = Callable[[], datetime]


class IsOwner(BaseFilter):
    async def __call__(self, event: TelegramObject, settings: Settings, **kwargs: Any) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in settings.owner_ids


router = Router(name="owner")
router.message.filter(F.chat.type == ChatType.PRIVATE, IsOwner())
router.callback_query.filter(IsOwner())

OWNER_HELP = (
    "🛠 <b>Owner panel</b>\n\n"
    "/today — today's schedule\n"
    "/tomorrow — tomorrow's schedule\n"
    "/services — enable/disable services\n"
    "/addservice — add a new service\n"
    "/staff — enable/disable specialists\n"
    "/addstaff <i>Name</i> — add a specialist\n"
    "/rating — average client rating"
)


class OwnerCancelCb(CallbackData, prefix="ocx"):
    booking: int
    sure: bool = False


class ToggleServiceCb(CallbackData, prefix="tsv"):
    service: int


class ToggleStaffCb(CallbackData, prefix="tst"):
    staff: int


class AddService(StatesGroup):
    name = State()
    duration = State()
    price = State()
    deposit = State()


def parse_amount(text: str) -> int | None:
    """'30' or '30.50' → 3050 (minor units)."""
    try:
        value = round(float(text.replace(",", ".").strip().lstrip("$€£")) * 100)
    except ValueError:
        return None
    return value if value >= 0 else None


@router.message(Command("owner", "help"))
async def owner_help(message: Message) -> None:
    await message.answer(OWNER_HELP)


# ---------- schedule ----------


async def _schedule(session: AsyncSession, settings: Settings, day_offset: int, clock: Clock):
    day = clock().astimezone(settings.tz).date() + timedelta(days=day_offset)
    start, end = day_bounds_utc(day, settings.tz)
    bookings = await repo.confirmed_between(session, start, end)

    title = f"📋 <b>{day.strftime('%A, %d %B')}</b>"
    if not bookings:
        return f"{title}\n\nNo bookings yet.", None

    lines, builder = [title, ""], InlineKeyboardBuilder()
    for b in bookings:
        paid = f" · 💳 {money(b.deposit, settings.currency)}" if b.deposit else ""
        lines.append(
            f"<b>{hhmm(b.start_at, settings.tz)}–{hhmm(b.end_at, settings.tz)}</b> {escape(b.service.name)}"
            f" · {escape(b.staff.name)}\n      👤 {escape(b.user_name)}{paid}"
        )
        builder.button(
            text=f"✖️ {hhmm(b.start_at, settings.tz)} {b.user_name[:20]}", callback_data=OwnerCancelCb(booking=b.id)
        )
    builder.adjust(2)
    lines.append(f"\nTotal: {len(bookings)} · {money(sum(b.service.price for b in bookings), settings.currency)}")
    return "\n".join(lines), builder.as_markup()


@router.message(Command("today"))
async def today(message: Message, session: AsyncSession, settings: Settings, clock: Clock) -> None:
    text, markup = await _schedule(session, settings, 0, clock)
    await message.answer(text, reply_markup=markup)


@router.message(Command("tomorrow"))
async def tomorrow(message: Message, session: AsyncSession, settings: Settings, clock: Clock) -> None:
    text, markup = await _schedule(session, settings, 1, clock)
    await message.answer(text, reply_markup=markup)


@router.callback_query(OwnerCancelCb.filter())
async def owner_cancel(
    cb: CallbackQuery, callback_data: OwnerCancelCb, bot: Bot, session: AsyncSession, settings: Settings, clock: Clock
) -> None:
    booking = await repo.get_booking(session, callback_data.booking)
    if booking is None or booking.status != BookingStatus.CONFIRMED:
        await cb.answer("Already cancelled.", show_alert=True)
        return
    if not callback_data.sure:
        builder = InlineKeyboardBuilder()
        builder.button(text="Yes, cancel and notify client", callback_data=OwnerCancelCb(booking=booking.id, sure=True))
        await cb.message.answer(
            f"Cancel booking #{booking.id}?\n{escape(booking.service.name)} · {when(booking.start_at, settings.tz)}"
            f" · {escape(booking.user_name)}",
            reply_markup=builder.as_markup(),
        )
        await cb.answer()
        return

    booking.status = BookingStatus.CANCELLED
    refund = " Your deposit will be refunded." if booking.deposit else ""
    await send_safe(
        bot,
        booking.user_id,
        f"😔 Unfortunately, your booking for <b>{escape(booking.service.name)}</b> on "
        f"{when(booking.start_at, settings.tz)} was cancelled by {escape(settings.business_name)}.{refund}\n"
        "Tap /start to choose another time.",
    )
    await cb.message.edit_text(f"❌ Booking #{booking.id} cancelled, client notified.")
    await cb.answer()
    await bk.announce_cancel(bot, booking, settings, clock(), by_client=False)


# ---------- services ----------


def _services_markup(services: list[Service], currency: str):
    builder = InlineKeyboardBuilder()
    for s in services:
        mark = "✅" if s.active else "⏸"
        dep = f", dep {money(s.deposit, currency)}" if s.deposit else ""
        builder.button(
            text=f"{mark} {s.name} · {s.duration_min}m · {money(s.price, currency)}{dep}",
            callback_data=ToggleServiceCb(service=s.id),
        )
    builder.adjust(1)
    return builder.as_markup()


@router.message(Command("services"))
async def services(message: Message, session: AsyncSession, settings: Settings) -> None:
    items = await repo.all_services(session)
    await message.answer(
        "💈 <b>Services</b> — tap to enable/disable. Add new ones with /addservice",
        reply_markup=_services_markup(items, settings.currency),
    )


@router.callback_query(ToggleServiceCb.filter())
async def toggle_service(
    cb: CallbackQuery, callback_data: ToggleServiceCb, session: AsyncSession, settings: Settings
) -> None:
    service = await session.get(Service, callback_data.service)
    if service:
        service.active = not service.active
        await session.flush()
    await cb.message.edit_reply_markup(
        reply_markup=_services_markup(await repo.all_services(session), settings.currency)
    )
    await cb.answer("Updated")


@router.message(Command("addservice"))
async def add_service_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddService.name)
    await message.answer("New service. What's the <b>name</b>? (/cancel to stop)")


@router.message(Command("cancel"))
async def cancel_fsm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled.")


@router.message(AddService.name, F.text)
async def add_service_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip()[:64])
    await state.set_state(AddService.duration)
    await message.answer("Duration in <b>minutes</b>? e.g. 45")


@router.message(AddService.duration, F.text)
async def add_service_duration(message: Message, state: FSMContext) -> None:
    if not message.text.strip().isdigit() or not 5 <= int(message.text) <= 600:
        await message.answer("Please send a number of minutes between 5 and 600.")
        return
    await state.update_data(duration=int(message.text))
    await state.set_state(AddService.price)
    await message.answer("<b>Price</b>? e.g. 30 or 29.90")


@router.message(AddService.price, F.text)
async def add_service_price(message: Message, state: FSMContext) -> None:
    price = parse_amount(message.text)
    if price is None:
        await message.answer("Please send a price, e.g. 30 or 29.90")
        return
    await state.update_data(price=price)
    await state.set_state(AddService.deposit)
    await message.answer("<b>Deposit</b> to pay when booking? Send 0 for no deposit.")


@router.message(AddService.deposit, F.text)
async def add_service_deposit(message: Message, state: FSMContext, session: AsyncSession, settings: Settings) -> None:
    deposit = parse_amount(message.text)
    data = await state.get_data()
    if deposit is None or deposit > data["price"]:
        await message.answer("Please send a deposit not higher than the price, or 0.")
        return
    service = await repo.add_service(session, data["name"], data["duration"], data["price"], deposit)
    await state.clear()
    await message.answer(
        f"✅ Added <b>{escape(service.name)}</b> · {service.duration_min} min · "
        f"{money(service.price, settings.currency)}"
        + (f" · deposit {money(deposit, settings.currency)}" if deposit else "")
    )


# ---------- staff ----------


def _staff_markup(staff: list[Staff]):
    builder = InlineKeyboardBuilder()
    for s in staff:
        builder.button(text=f"{'✅' if s.active else '⏸'} {s.name}", callback_data=ToggleStaffCb(staff=s.id))
    builder.adjust(2)
    return builder.as_markup()


@router.message(Command("staff"))
async def staff(message: Message, session: AsyncSession) -> None:
    await message.answer(
        "👤 <b>Specialists</b> — tap to enable/disable. Add with /addstaff Name",
        reply_markup=_staff_markup(await repo.all_staff(session)),
    )


@router.callback_query(ToggleStaffCb.filter())
async def toggle_staff(cb: CallbackQuery, callback_data: ToggleStaffCb, session: AsyncSession) -> None:
    member = await session.get(Staff, callback_data.staff)
    if member:
        member.active = not member.active
        await session.flush()
    await cb.message.edit_reply_markup(reply_markup=_staff_markup(await repo.all_staff(session)))
    await cb.answer("Updated")


@router.message(Command("addstaff"))
async def add_staff(message: Message, command: CommandObject, session: AsyncSession) -> None:
    name = (command.args or "").strip()[:64]
    if not name:
        await message.answer("Usage: /addstaff Name")
        return
    await repo.add_staff(session, name)
    await message.answer(f"✅ Added specialist <b>{escape(name)}</b>")


@router.message(Command("rating"))
async def rating(message: Message, session: AsyncSession) -> None:
    avg, count = await repo.average_rating(session)
    if not count:
        await message.answer("No ratings yet. Clients are asked to rate their visit automatically.")
        return
    await message.answer(f"⭐ Average rating: <b>{avg:.1f}</b> / 5 from {count} visit(s)")
