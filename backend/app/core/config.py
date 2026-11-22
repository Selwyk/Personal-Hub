from functools import lru_cache
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from typing import Literal
from uuid import UUID

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.instance import database_name, role_name
from app.core.product import DOMAIN


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: str
    totp_encryption_key: str
    app_public_url: str = "http://localhost:5173"
    webauthn_rp_id: str = "localhost"
    webauthn_origins: str = "http://localhost:5173"
    cookie_secure: bool = False
    session_idle_timeout_seconds: int = 86400
    session_absolute_timeout_seconds: int = 604800
    trusted_device_days: int = 30
    app_timezone: str = "Europe/Madrid"
    import_input_path: str | None = None
    tasks_enabled: bool = False
    isolation_required: bool = False
    instance_id: UUID | None = None
    instance_domain: Literal["personal", "work"] | None = None
    migration_database_url: SecretStr | None = None
    bootstrap_username: str = Field(default="admin", min_length=1, max_length=100)
    bootstrap_password: SecretStr | None = None

    @property
    def origins(self):
        return [x.strip().rstrip("/") for x in self.webauthn_origins.split(",")]

    @property
    def session_cookie(self):
        if self.isolation_required:
            return ("__Host-" if self.cookie_secure else "") + f"hub_{self.instance_id.hex}_session"
        return "__Host-hub_session" if self.cookie_secure else "hub_session"

    @property
    def device_cookie(self):
        if self.isolation_required:
            return ("__Host-" if self.cookie_secure else "") + f"hub_{self.instance_id.hex}_device"
        return "__Host-hub_device" if self.cookie_secure else "hub_device"

    @model_validator(mode="after")
    def check_security(self):
        if self.isolation_required:
            if not self.instance_id or self.instance_domain != DOMAIN or len(self.origins) != 1:
                raise ValueError("Isolated instances require identity, domain and one origin")
            for raw, purpose in ((self.database_url, "app"),
                    (self.migration_database_url.get_secret_value() if self.migration_database_url else None, "migrate")):
                if raw is None:
                    continue
                try:
                    url = make_url(raw)
                    valid = (url.drivername == "postgresql+psycopg"
                             and url.database == database_name(self.instance_id, self.instance_domain)
                             and url.username == role_name(self.instance_id, purpose) and bool(url.password))
                except Exception:
                    valid = False
                if not valid:
                    raise ValueError("Isolated database name or role does not match instance")
        elif self.instance_id or self.instance_domain or self.migration_database_url:
            raise ValueError("Instance configuration requires ISOLATION_REQUIRED")
        if self.bootstrap_password and not 15 <= len(self.bootstrap_password.get_secret_value()) <= 1024:
            raise ValueError("BOOTSTRAP_PASSWORD must contain 15 to 1024 characters")
        Fernet(self.totp_encryption_key.encode())
        ZoneInfo(self.app_timezone)
        public = urlparse(self.app_public_url)
        if public.path not in ("", "/") or public.query or public.fragment:
            raise ValueError("APP_PUBLIC_URL must be an origin")
        if self.app_public_url.rstrip("/") not in self.origins:
            raise ValueError("Public origin must be allowed")
        for origin in self.origins:
            parsed = urlparse(origin)
            if parsed.hostname != self.webauthn_rp_id:
                raise ValueError("Origins must match RP ID")
            if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
                raise ValueError("Non-local origins require HTTPS")
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                raise ValueError("Invalid WebAuthn origin")
        if public.scheme == "https" and not self.cookie_secure:
            raise ValueError("HTTPS requires secure cookies")
        if public.hostname not in ("localhost", "127.0.0.1") and not self.cookie_secure:
            raise ValueError("Production requires secure cookies")
        if min(self.session_idle_timeout_seconds, self.session_absolute_timeout_seconds,
               self.trusted_device_days) <= 0:
            raise ValueError("Timeouts must be positive")
        return self


@lru_cache
def settings():
    return Settings()
