from datetime import time
from functools import cached_property, lru_cache
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a .env file)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    owner_ids: list[int] = []
    database_url: str = "sqlite+aiosqlite:///bot.db"

    # Business
    business_name: str = "Demo Barbershop"
    timezone: str = "Europe/London"
    work_days: list[int] = [0, 1, 2, 3, 4, 5]  # Monday = 0
    work_start: time = time(10, 0)
    work_end: time = time(20, 0)
    slot_step_minutes: int = 30
    booking_days_ahead: int = 7
    min_lead_minutes: int = 60  # how soon a slot can be booked

    # Payments (Telegram Payments, e.g. Stripe). Leave empty to book without a deposit.
    payment_provider_token: SecretStr | None = None
    currency: str = "USD"
    pending_payment_minutes: int = 15  # slot is held this long while the client pays

    # Policy
    free_cancel_hours: int = 24  # deposit is refundable if cancelled earlier than this

    # Reviews
    review_delay_minutes: int = 120  # ask for a rating this long after the visit ends
    review_url: str | None = None  # e.g. your Google Maps review link

    # Branding (optional): public image URLs
    welcome_image_url: str | None = None  # large banner above the greeting (link preview)
    invoice_photo_url: str | None = None  # picture on the payment card

    seed_demo: bool = True  # create sample services and staff on first start

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def payments_enabled(self) -> bool:
        return bool(self.payment_provider_token and self.payment_provider_token.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
