from collections import OrderedDict
from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic
from typing import Literal


@dataclass(frozen=True, repr=False)
class CatalogView:
    kind: Literal["categories", "products"]
    category_code: str | None = None
    query: str | None = None
    page: int = 0
    back: str | None = None


class NavigationStore:
    def __init__(self) -> None:
        # shortcut: ephemeral single-process navigation; reopen /shop after restart or expiry.
        self.entries: OrderedDict[str, tuple[float, int, CatalogView]] = OrderedDict()

    def _prune(self) -> None:
        now = monotonic()
        while self.entries and next(iter(self.entries.values()))[0] <= now:
            self.entries.popitem(last=False)

    def remember(self, actor_id: int, view: CatalogView) -> str:
        self._prune()
        if len(self.entries) >= 4096:
            self.entries.popitem(last=False)
        token = token_urlsafe(12)
        self.entries[token] = (monotonic() + 600, actor_id, view)
        return token

    def resolve(self, actor_id: int, token: str) -> CatalogView | None:
        self._prune()
        entry = self.entries.get(token)
        return entry[2] if entry is not None and entry[1] == actor_id else None
