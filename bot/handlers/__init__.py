from aiogram import Router

from bot.handlers import client, owner, payments


def setup_routers() -> Router:
    root = Router(name="root")
    # Owner commands first; client router handles everything else in private chats.
    root.include_routers(owner.router, payments.router, client.router)
    return root
