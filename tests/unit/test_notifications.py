from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from telebotshop.domain.errors import InvalidInput
from telebotshop.domain.models.notifications import (
    NotificationKind,
    NotificationRequest,
    NotificationResult,
    NotificationState,
)


def test_notification_boundary_accepts_only_typed_metadata_and_explicit_outcomes():
    request = NotificationRequest(
        uuid4(),
        NotificationKind.LOW_STOCK,
        uuid4(),
        9001,
        datetime.now(UTC) + timedelta(seconds=60),
    )
    assert "9001" not in repr(request)
    for state, message in [
        (NotificationState.SENT, None),
        (NotificationState.UNCERTAIN, 1),
        (NotificationState.READY, None),
    ]:
        with pytest.raises(InvalidInput):
            NotificationResult(state, message)
    assert NotificationResult(NotificationState.SENT, 12).message_id == 12
    with pytest.raises(InvalidInput):
        NotificationRequest(uuid4(), "LOW_STOCK", uuid4(), True, datetime.now(UTC))
