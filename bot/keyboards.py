from datetime import date, datetime

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.db.models import Service, Staff
from bot.services.formatting import hhmm, money


class MenuCb(CallbackData, prefix="m"):
    action: str  # book | my


class ServiceCb(CallbackData, prefix="sv"):
    service: int


class StaffCb(CallbackData, prefix="st"):
    service: int
    staff: int


class DayCb(CallbackData, prefix="d"):
    service: int
    staff: int
    day: str  # YYYYMMDD


class SlotCb(CallbackData, prefix="sl"):
    service: int
    staff: int
    ts: int  # unix timestamp (UTC)


class ConfirmCb(CallbackData, prefix="cf"):
    service: int
    staff: int
    ts: int


class CancelCb(CallbackData, prefix="cx"):
    booking: int
    sure: bool = False


class AttendCb(CallbackData, prefix="at"):
    booking: int


class RateCb(CallbackData, prefix="rt"):
    booking: int
    score: int


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Book an appointment", callback_data=MenuCb(action="book").pack())],
            [InlineKeyboardButton(text="🗂 My bookings", callback_data=MenuCb(action="my").pack())],
        ]
    )


def services_kb(services: list[Service], currency: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        kb.button(
            text=f"{s.name} · {s.duration_min} min · {money(s.price, currency)}",
            callback_data=ServiceCb(service=s.id),
        )
    kb.adjust(1)
    return kb.as_markup()


def staff_kb(service_id: int, staff: list[Staff]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for st in staff:
        kb.button(text=f"👤 {st.name}", callback_data=StaffCb(service=service_id, staff=st.id))
    kb.button(text="← Back", callback_data=MenuCb(action="book"))
    kb.adjust(1)
    return kb.as_markup()


def days_kb(service_id: int, staff_id: int, days: list[date]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in days:
        kb.button(
            text=d.strftime("%a %d %b"),
            callback_data=DayCb(service=service_id, staff=staff_id, day=d.strftime("%Y%m%d")),
        )
    kb.button(text="← Back", callback_data=ServiceCb(service=service_id))
    kb.adjust(*([2] * (len(days) // 2)), 1)
    return kb.as_markup()


def slots_kb(service_id: int, staff_id: int, slots: list[datetime], settings: Settings) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in slots:
        kb.button(
            text=hhmm(s, settings.tz),
            callback_data=SlotCb(service=service_id, staff=staff_id, ts=int(s.timestamp())),
        )
    kb.adjust(4)
    kb.row(InlineKeyboardButton(text="← Back", callback_data=StaffCb(service=service_id, staff=staff_id).pack()))
    return kb.as_markup()


def confirm_kb(service_id: int, staff_id: int, ts: int, day: str, pay_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=pay_text, callback_data=ConfirmCb(service=service_id, staff=staff_id, ts=ts).pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Pick another time",
                    callback_data=DayCb(service=service_id, staff=staff_id, day=day).pack(),
                )
            ],
        ]
    )


def booking_actions_kb(booking_id: int, with_attend: bool = False) -> InlineKeyboardMarkup:
    row = []
    if with_attend:
        row.append(InlineKeyboardButton(text="👍 I'll be there", callback_data=AttendCb(booking=booking_id).pack()))
    row.append(InlineKeyboardButton(text="✖️ Cancel", callback_data=CancelCb(booking=booking_id).pack()))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Yes, cancel", callback_data=CancelCb(booking=booking_id, sure=True).pack()),
                InlineKeyboardButton(text="Keep it", callback_data=MenuCb(action="my").pack()),
            ]
        ]
    )


def rating_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{i}⭐", callback_data=RateCb(booking=booking_id, score=i).pack())
                for i in range(1, 6)
            ]
        ]
    )
