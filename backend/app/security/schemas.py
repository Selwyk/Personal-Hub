from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class TotpCode(Input):
    code: str = Field(min_length=1, max_length=32)
    trust_device: bool = False
    device_name: str = Field(default="Navegador", min_length=1, max_length=100)


class ChallengeCode(TotpCode):
    challenge_id: UUID


class Named(Input):
    name: str = Field(min_length=1, max_length=100)


class KeyResult(Named):
    challenge_id: UUID
    credential: dict


class KeyAuth(Input):
    challenge_id: UUID
    credential: dict
    trust_device: bool = False
    device_name: str = Field(default="Navegador", min_length=1, max_length=100)
    password: str | None = Field(default=None, max_length=1024)


class StepUp(Input):
    password: str = Field(max_length=1024)
    code: str = Field(max_length=32)


class PasswordChange(Input):
    new_password: str = Field(min_length=15, max_length=1024)


class TokenCreate(Named):
    expires_at: datetime | None = None
