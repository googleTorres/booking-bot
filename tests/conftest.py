from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import Settings
from bot.db.seed import seed_demo
from bot.db.session import create_engine, create_sessionmaker, init_db

# Monday 2 Nov 2026, 08:00 in London (UTC+0 in winter)
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
OWNER = 900
CLIENT = 100


def make_settings(**overrides) -> Settings:
    base = dict(bot_token="123:abc", owner_ids=[OWNER], timezone="Europe/London", seed_demo=True)
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
async def sm():
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sessionmaker = create_sessionmaker(engine)
    async with sessionmaker() as s:
        await seed_demo(s)
    yield sessionmaker
    await engine.dispose()


@pytest.fixture
def bot():
    b = AsyncMock()
    b.send_message.return_value = MagicMock(message_id=1)
    return b


@pytest.fixture
def clock():
    state = SimpleNamespace(now=NOW)

    def _clock():
        return state.now

    _clock.state = state
    return _clock
