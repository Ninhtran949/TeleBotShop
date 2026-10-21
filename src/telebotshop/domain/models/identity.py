import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from telebotshop.domain.errors import InvalidInput


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    SUPPORT = "SUPPORT"


def validate_telegram_id(telegram_id: int) -> None:
    if type(telegram_id) is not int or not 0 < telegram_id < 2**52:
        raise InvalidInput("Invalid identity")


@dataclass(frozen=True, repr=False)
class CustomerIdentity:
    telegram_id: int
    username: str | None
    display_name: str | None
    language: str = "vi"

    def __post_init__(self) -> None:
        validate_telegram_id(self.telegram_id)
        if (
            self.username is not None
            and (
                not isinstance(self.username, str)
                or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", self.username)
            )
            or self.display_name is not None
            and (
                not isinstance(self.display_name, str)
                or not 1 <= len(self.display_name.strip()) <= 129
            )
            or not isinstance(self.language, str)
            or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,3}", self.language)
            or len(self.language) > 35
        ):
            raise InvalidInput("Invalid identity")


@dataclass(frozen=True, repr=False)
class Profile:
    id: UUID
    telegram_id: int
    username: str | None
    display_name: str | None
    language: str
    registered_at: datetime
    last_seen_at: datetime
