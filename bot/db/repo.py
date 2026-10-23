from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Booking, BookingStatus, Service, Staff

# ---------- catalog ----------


async def active_services(session: AsyncSession) -> list[Service]:
    return list((await session.scalars(select(Service).where(Service.active.is_(True)).order_by(Service.id))).all())


async def all_services(session: AsyncSession) -> list[Service]:
    return list((await session.scalars(select(Service).order_by(Service.id))).all())


async def active_staff(session: AsyncSession) -> list[Staff]:
    return list((await session.scalars(select(Staff).where(Staff.active.is_(True)).order_by(Staff.id))).all())


async def all_staff(session: AsyncSession) -> list[Staff]:
    return list((await session.scalars(select(Staff).order_by(Staff.id))).all())


async def add_service(session: AsyncSession, name: str, duration_min: int, price: int, deposit: int) -> Service:
    service = Service(name=name, duration_min=duration_min, price=price, deposit=deposit)
    session.add(service)
    await session.flush()
    return service


async def add_staff(session: AsyncSession, name: str) -> Staff:
    staff = Staff(name=name)
    session.add(staff)
    await session.flush()
    return staff


# ---------- bookings ----------


def _blocking(now: datetime, pending_ttl: timedelta):
    """Bookings that occupy a slot: confirmed ones, and pending ones still within the payment window."""
    return or_(
        Booking.status == BookingStatus.CONFIRMED,
        and_(Booking.status == BookingStatus.PENDING, Booking.created_at > now - pending_ttl),
    )


async def busy_intervals(
    session: AsyncSession,
    staff_id: int,
    start: datetime,
    end: datetime,
    now: datetime,
    pending_ttl: timedelta,
    exclude_id: int | None = None,
) -> list[tuple[datetime, datetime]]:
    stmt = select(Booking.start_at, Booking.end_at).where(
        Booking.staff_id == staff_id,
        Booking.start_at < end,
        Booking.end_at > start,
        _blocking(now, pending_ttl),
    )
    if exclude_id is not None:
        stmt = stmt.where(Booking.id != exclude_id)
    return [(r.start_at, r.end_at) for r in (await session.execute(stmt)).all()]


async def has_conflict(
    session: AsyncSession,
    staff_id: int,
    start: datetime,
    end: datetime,
    now: datetime,
    pending_ttl: timedelta,
    exclude_id: int | None = None,
) -> bool:
    return bool(await busy_intervals(session, staff_id, start, end, now, pending_ttl, exclude_id))


async def create_booking(
    session: AsyncSession,
    user_id: int,
    user_name: str,
    service: Service,
    staff: Staff,
    start: datetime,
    status: str,
    now: datetime,
) -> Booking:
    booking = Booking(
        user_id=user_id,
        user_name=user_name,
        service_id=service.id,
        staff_id=staff.id,
        start_at=start,
        end_at=start + timedelta(minutes=service.duration_min),
        status=status,
        deposit=service.deposit,
        created_at=now,
    )
    session.add(booking)
    await session.flush()
    await session.refresh(booking)  # load service/staff relationships
    return booking


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    return await session.get(Booking, booking_id)


async def user_upcoming(session: AsyncSession, user_id: int, now: datetime) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.user_id == user_id, Booking.status == BookingStatus.CONFIRMED, Booking.start_at > now)
        .order_by(Booking.start_at)
    )
    return list((await session.scalars(stmt)).all())


async def confirmed_between(session: AsyncSession, start: datetime, end: datetime) -> list[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.status == BookingStatus.CONFIRMED, Booking.start_at >= start, Booking.start_at < end)
        .order_by(Booking.start_at)
    )
    return list((await session.scalars(stmt)).all())


async def average_rating(session: AsyncSession) -> tuple[float | None, int]:
    row = (await session.execute(select(func.avg(Booking.rating), func.count(Booking.rating)))).one()
    return (float(row[0]) if row[0] is not None else None), row[1]
