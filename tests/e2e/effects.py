"""Persist before an external payment/refund; an interrupted attempt needs reconciliation."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from tests.e2e.safety import E2EBlocked


class EffectJournal:
    def __init__(self, root: Path, order_id: UUID, kind: str):
        if not isinstance(order_id, UUID) or kind not in {"payment", "refund"}:
            raise E2EBlocked("Invalid test effect reference")
        self.path = root / ".cache/t14-e2e/effects" / f"{kind}.{order_id}.json"

    def begin(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("x", encoding="utf-8") as stream:
                json.dump({"state": "ATTEMPTED", "at": datetime.now(UTC).isoformat()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise E2EBlocked(
                "Effect already attempted; reconcile its receipt before proceeding"
            ) from None

    def confirm(self):
        if not self.path.is_file():
            raise E2EBlocked("Cannot confirm an effect that was never attempted")
        # Keeping the file, even if this write fails, prevents a second external attempt.
        self.path.write_text(
            json.dumps({"state": "CONFIRMED", "at": datetime.now(UTC).isoformat()}), "utf-8"
        )
