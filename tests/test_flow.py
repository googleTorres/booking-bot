"""Booking flow through real handlers with a mocked Telegram API."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from aiogram.types import CallbackQuery, Chat, Message, PreCheckoutQuery, SuccessfulPayment, User

from bot import keyboards as kb
from bot.db import repo
from bot.db.models import Booking, BookingStatus, Service
from bot.handlers import client, payments
from bot.services.scheduler import run_due
from tests.conftest import CLIENT, NOW, OWNER, make_settings

USER = User(id=CLIENT, is_bot=False, first_name="Emma", last_name="Stone")
OTHER = User(id=101, is_bot=False, first_name="Tom")
CHAT = Chat(id=CLIENT, type="private")
SLOT = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)  # Tuesday 14:00 London


def cb(bot, user=USER) -> CallbackQuery:
    q = CallbackQuery(id="1", from_user=user, chat_instance="x", data="x").as_(bot)
    msg = AsyncMock()
    msg.chat = CHAT
    object.__setattr__(q, "message", msg)
    return q


async def confirm(sm, bot, settings, clock, user=USER, service_id=1, staff_id=1, ts=SLOT):
    q = cb(bot, user)
    async with sm() as s:
        await client.create_booking(
            q, kb.ConfirmCb(service=service_id, staff=staff_id, ts=int(ts.timestamp())), bot, s, settings, clock
        )
        await s.commit()
    return q


async def bookings(sm) -> list[Booking]:
    from sqlalchemy import select

    async with sm() as s:
        return list((await s.scalars(select(Booking).order_by(Booking.id))).all())


def sent_to(bot, chat_id) -> list[str]:
    return [c.args[1] for c in bot.send_message.await_args_list if c.args[0] == chat_id]


# ---------- without payments ----------


async def test_slots_hide_taken_time(sm, bot, clock):
    settings = make_settings()
    await confirm(sm, bot, settings, clock)
    from bot.services.slots import slots_for

    async with sm() as s:
        service, staff = await client._load(s, 1, 1)
        free = await slots_for(s, settings, service, staff, SLOT.date(), NOW)
    assert SLOT not in free
    assert SLOT - timedelta(minutes=30) not in free  # 13:30 + 45 min would overlap
    assert SLOT + timedelta(minutes=60) in free


async def test_booking_without_deposit_confirms_and_notifies_owner(sm, bot, clock):
    settings = make_settings()
    q = await confirm(sm, bot, settings, clock)
    [b] = await bookings(sm)
    assert b.status == BookingStatus.CONFIRMED
    assert b.end_at - b.start_at == timedelta(minutes=45)
    assert "You're booked" in q.message.edit_text.await_args.args[0]
    assert any("New booking" in t for t in sent_to(bot, OWNER))
    bot.send_invoice.assert_not_awaited()


async def test_same_slot_cannot_be_booked_twice(sm, bot, clock):
    settings = make_settings()
    await confirm(sm, bot, settings, clock)
    await confirm(sm, bot, settings, clock, user=OTHER, ts=SLOT + timedelta(minutes=15))
    assert len(await bookings(sm)) == 1


async def test_other_staff_is_independent(sm, bot, clock):
    settings = make_settings()
    await confirm(sm, bot, settings, clock)
    await confirm(sm, bot, settings, clock, user=OTHER, staff_id=2)
    assert len(await bookings(sm)) == 2


# ---------- with payments ----------


def paid_settings():
    return make_settings(payment_provider_token="TEST:stripe")


async def test_deposit_flow_invoice_checkout_payment(sm, bot, clock):
    settings = paid_settings()
    await confirm(sm, bot, settings, clock)
    [b] = await bookings(sm)
    assert b.status == BookingStatus.PENDING and b.deposit == 1000

    invoice = bot.send_invoice.await_args.kwargs
    assert invoice["payload"] == f"booking:{b.id}" and invoice["prices"][0].amount == 1000

    query = PreCheckoutQuery(
        id="pc", from_user=USER, currency="USD", total_amount=1000, invoice_payload=invoice["payload"]
    ).as_(bot)
    async with sm() as s:
        await payments.pre_checkout(query, s, settings, clock)
    assert bot.await_args.args[0].ok is True  # AnswerPreCheckoutQuery(ok=True)

    msg = Message(
        message_id=5,
        date=NOW,
        chat=CHAT,
        from_user=USER,
        successful_payment=SuccessfulPayment(
            currency="USD",
            total_amount=1000,
            invoice_payload=invoice["payload"],
            telegram_payment_charge_id="tg_1",
            provider_payment_charge_id="ch_stripe_1",
        ),
    ).as_(bot)
    async with sm() as s:
        await payments.successful_payment(msg, bot, s, settings, clock)
        await s.commit()
    [b] = await bookings(sm)
    assert b.status == BookingStatus.CONFIRMED and b.charge_id == "ch_stripe_1"
    assert any("New booking" in t for t in sent_to(bot, OWNER))


async def test_pending_hold_blocks_slot_then_expires(sm, bot, clock):
    settings = paid_settings()
    await confirm(sm, bot, settings, clock)
    await confirm(sm, bot, settings, clock, user=OTHER)
    assert len(await bookings(sm)) == 1  # held while the first client pays

    clock.state.now = NOW + timedelta(minutes=16)
    async with sm() as s:
        stats = await run_due(s, bot, settings, clock())
        await s.commit()
    assert stats.expired == 1

    await confirm(sm, bot, settings, clock, user=OTHER)
    assert [b.status for b in await bookings(sm)] == [BookingStatus.EXPIRED, BookingStatus.PENDING]


async def test_checkout_rejected_when_amount_changed(sm, bot, clock):
    settings = paid_settings()
    await confirm(sm, bot, settings, clock)
    query = PreCheckoutQuery(id="pc", from_user=USER, currency="USD", total_amount=1, invoice_payload="booking:1").as_(
        bot
    )
    async with sm() as s:
        await payments.pre_checkout(query, s, settings, clock)
    assert bot.await_args.args[0].ok is False


# ---------- reminders, cancellation, rating ----------


async def test_reminders_sent_once_then_rating(sm, bot, clock):
    settings = make_settings()
    await confirm(sm, bot, settings, clock)  # booked > 24h ahead (Mon 08:00 → Tue 14:00)
    bot.send_message.reset_mock()

    async def tick(at):
        clock.state.now = at
        async with sm() as s:
            st = await run_due(s, bot, settings, at)
            await s.commit()
        return st

    assert (await tick(SLOT - timedelta(hours=25))).reminded_24h == 0
    assert (await tick(SLOT - timedelta(hours=23))).reminded_24h == 1
    assert (await tick(SLOT - timedelta(hours=22))).reminded_24h == 0  # not repeated
    assert (await tick(SLOT - timedelta(hours=1))).reminded_2h == 1
    assert (await tick(SLOT + timedelta(hours=3))).review_asked == 1
    [b] = await bookings(sm)
    assert b.status == BookingStatus.COMPLETED

    q = cb(bot)
    async with sm() as s:
        await client.rate(q, kb.RateCb(booking=b.id, score=2), bot, s, settings)
        await s.commit()
    assert (await bookings(sm))[0].rating == 2
    assert any("Low rating" in t for t in sent_to(bot, OWNER))


async def test_cancel_policy_refundable_vs_late(sm, bot, clock):
    settings = paid_settings()
    async with sm() as s:
        service = await s.get(Service, 1)
        staff = (await repo.active_staff(s))[0]
        b = await repo.create_booking(s, CLIENT, "Emma", service, staff, SLOT, BookingStatus.CONFIRMED, NOW)
        await s.commit()

    # 30h before → refundable
    clock.state.now = SLOT - timedelta(hours=30)
    q = cb(bot)
    async with sm() as s:
        await client.cancel_booking(q, kb.CancelCb(booking=b.id), bot, s, settings, clock)
    assert "will be refunded" in q.message.edit_text.await_args.args[0]

    # 3h before → late
    clock.state.now = SLOT - timedelta(hours=3)
    q = cb(bot)
    async with sm() as s:
        await client.cancel_booking(q, kb.CancelCb(booking=b.id), bot, s, settings, clock)
        assert "non-refundable" in q.message.edit_text.await_args.args[0]
        await client.cancel_booking(q, kb.CancelCb(booking=b.id, sure=True), bot, s, settings, clock)
        await s.commit()
    assert (await bookings(sm))[0].status == BookingStatus.CANCELLED
    assert any("non-refundable" in t for t in sent_to(bot, OWNER))


async def test_other_user_cannot_cancel(sm, bot, clock):
    settings = make_settings()
    await confirm(sm, bot, settings, clock)
    q = cb(bot, user=OTHER)
    async with sm() as s:
        await client.cancel_booking(q, kb.CancelCb(booking=1, sure=True), bot, s, settings, clock)
        await s.commit()
    assert (await bookings(sm))[0].status == BookingStatus.CONFIRMED


# ---------- branding ----------


async def test_greeting_uses_large_banner_when_configured(bot):
    msg = Message(message_id=1, date=NOW, chat=CHAT, from_user=USER).as_(bot)
    await client.start(msg, make_settings(welcome_image_url="https://example.com/banner.png"))
    preview = bot.await_args.args[0].link_preview_options
    assert preview.url == "https://example.com/banner.png" and preview.show_above_text and preview.prefer_large_media

    await client.start(msg, make_settings())
    assert bot.await_args.args[0].link_preview_options is None


async def test_invoice_photo(sm, bot, clock):
    settings = make_settings(payment_provider_token="TEST:stripe", invoice_photo_url="https://example.com/i.png")
    await confirm(sm, bot, settings, clock)
    assert bot.send_invoice.await_args.kwargs["photo_url"] == "https://example.com/i.png"
