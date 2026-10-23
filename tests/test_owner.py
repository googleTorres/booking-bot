from aiogram.types import Message, User

from bot.handlers import owner
from tests.conftest import NOW, OWNER, make_settings
from tests.test_flow import CHAT, OTHER, bookings, cb, confirm


async def test_is_owner_filter():
    settings = make_settings()
    f = owner.IsOwner()
    as_owner = Message(message_id=1, date=NOW, chat=CHAT, from_user=User(id=OWNER, is_bot=False, first_name="O"))
    as_client = Message(message_id=1, date=NOW, chat=CHAT, from_user=OTHER)
    assert await f(as_owner, settings=settings)
    assert not await f(as_client, settings=settings)


async def test_tomorrow_schedule_lists_booking_with_total(sm, bot, clock):
    settings = make_settings()
    await confirm(sm, bot, settings, clock)  # Tuesday 14:00, "tomorrow" for NOW
    async with sm() as s:
        text, markup = await owner._schedule(s, settings, 1, clock)
        empty, _ = await owner._schedule(s, settings, 0, clock)
    assert "14:00–14:45" in text and "Haircut" in text and "Emma" in text
    assert "Total: 1 · $30" in text
    assert markup is not None
    assert "No bookings yet" in empty


async def test_owner_cancel_notifies_client(sm, bot, clock):
    settings = make_settings()
    await confirm(sm, bot, settings, clock)
    [b] = await bookings(sm)
    q = cb(bot, user=User(id=OWNER, is_bot=False, first_name="O"))
    async with sm() as s:
        await owner.owner_cancel(q, owner.OwnerCancelCb(booking=b.id, sure=True), bot, s, settings, clock)
        await s.commit()
    assert (await bookings(sm))[0].status == "cancelled"
    assert any(c.args[0] == b.user_id and "cancelled" in c.args[1] for c in bot.send_message.await_args_list)
