import asyncio
import logging
from time import monotonic

from telebotshop.application.services.fulfillment import FulfillmentService
from telebotshop.application.services.notifications import NotificationService

logger = logging.getLogger(__name__)


async def run_forever(
    service: FulfillmentService, notifications: NotificationService, *, poll_seconds: float
) -> None:
    scan_after = 0.0
    while True:
        await service.recover()
        delivered = await service.run_once()
        notified = False
        try:
            if monotonic() >= scan_after:
                scan_after = monotonic() + 10
                await notifications.discover()
            notified = await notifications.run_once()
        except Exception:
            # Operational notification failures must not stop durable paid delivery work.
            logger.warning("notification_worker_failed")
        if not delivered and not notified:
            await asyncio.sleep(poll_seconds)
