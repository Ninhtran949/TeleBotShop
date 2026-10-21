import re
import unicodedata
from dataclasses import dataclass

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.catalog import validate_price
from telebotshop.domain.models.identity import validate_telegram_id


@dataclass(frozen=True, repr=False)
class PreCheckout:
    query_id: str
    buyer_telegram_id: int
    currency: str
    amount: int
    payload: str

    def __post_init__(self) -> None:
        validate_telegram_id(self.buyer_telegram_id)
        validate_price(self.amount)
        if (
            not isinstance(self.query_id, str)
            or not 1 <= len(self.query_id) <= 256
            or not self.query_id.strip()
            or any(unicodedata.category(char) == "Cc" for char in self.query_id)
            or self.currency != "XTR"
            or not isinstance(self.payload, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{32}", self.payload)
        ):
            raise InvalidInput("Invalid pre-checkout query")
        try:
            self.query_id.encode("utf-8")
        except UnicodeEncodeError:
            raise InvalidInput("Invalid pre-checkout query") from None
