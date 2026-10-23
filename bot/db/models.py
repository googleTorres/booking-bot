from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores timezone-aware datetimes as UTC and always returns aware UTC values
    (SQLite drops tzinfo, PostgreSQL keeps it — this makes both behave the same)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed, use timezone-aware values")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(default=True)


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    duration_min: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)  # minor units (cents)
    deposit: Mapped[int] = mapped_column(Integer, default=0)  # minor units, 0 = no prepayment
    active: Mapped[bool] = mapped_column(default=True)


class BookingStatus:
    PENDING = "pending"  # waiting for deposit payment
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # deposit not paid in time
    COMPLETED = "completed"


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_name: Mapped[str] = mapped_column(String(256), default="")
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), default=BookingStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    deposit: Mapped[int] = mapped_column(Integer, default=0)
    charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    reminded_24h: Mapped[bool] = mapped_column(default=False)
    reminded_2h: Mapped[bool] = mapped_column(default=False)
    review_asked: Mapped[bool] = mapped_column(default=False)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)

    service: Mapped[Service] = relationship(lazy="joined")
    staff: Mapped[Staff] = relationship(lazy="joined")

    __table_args__ = (Index("ix_bookings_staff_start", "staff_id", "start_at"),)
