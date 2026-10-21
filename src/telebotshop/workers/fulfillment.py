import asyncio

from telebotshop.application.services.fulfillment import FulfillmentService


async def run_forever(service: FulfillmentService, *, poll_seconds: float) -> None:
    while True:
        await service.recover()
        if not await service.run_once():
            await asyncio.sleep(poll_seconds)
