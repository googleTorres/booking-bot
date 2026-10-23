from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Service, Staff

DEMO_SERVICES = [
    ("Haircut", 45, 3000, 1000),
    ("Beard trim", 30, 2000, 500),
    ("Haircut + Beard", 75, 4500, 1500),
]
DEMO_STAFF = ["Alex", "Maria"]


async def seed_demo(session: AsyncSession) -> bool:
    """Create sample services and staff if the catalog is empty. Returns True if seeded."""
    if (await session.execute(select(func.count()).select_from(Service))).scalar_one():
        return False
    session.add_all(Service(name=n, duration_min=d, price=p, deposit=dep) for n, d, p, dep in DEMO_SERVICES)
    session.add_all(Staff(name=n) for n in DEMO_STAFF)
    await session.commit()
    return True
