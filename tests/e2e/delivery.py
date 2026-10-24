"""Verify the actual delivered message/file and complete the explicitly fake manual task."""

import asyncio
from datetime import timedelta

from sqlalchemy import text

from tests.e2e.checkout import read_order
from tests.e2e.safety import E2EBlocked

FAKE_DOCUMENT = b"T14 FAKE TEST DOCUMENT. No commercial value.\n"


def select_delivery(messages, bot_id, sending_at, completed_at, expected_text):
    # shortcut: allow two seconds of clock skew; investigate rather than widen on ambiguity.
    tolerance = timedelta(seconds=2)
    matches = [
        message
        for message in messages
        if message.sender_id == bot_id
        and message.date is not None
        and sending_at - tolerance <= message.date <= completed_at + tolerance
        and (
            message.document is not None
            if expected_text is None
            else message.raw_text == expected_text
        )
    ]
    if len(matches) != 1:
        raise E2EBlocked(
            "Expected exactly one buyer-side delivery during the confirmed send attempt"
        )
    return matches[0]


async def verify_delivery(client, driver, evidence, engine, user, sku, order):
    expected = (
        None
        if sku in {"T14_FILE", "T14_DOC"}
        else "T14 FAKE reusable text. No commercial value."
        if sku == "T14_TEXT"
        else "T14 FAKE: Don thu cong test dang cho nhan vien xu ly."
        if sku == "T14_MANUAL"
        else f"T14-FAKE-{sku}-0001"
    )
    async with asyncio.timeout(45):
        while True:
            async with engine.connect() as connection:
                attempts = (
                    (
                        await connection.execute(
                            text(
                                "SELECT message_id,sending_at,completed_at FROM delivery_attempts "
                                "WHERE work_id=:work AND state='SENT'"
                            ),
                            {"work": order["work_id"]},
                        )
                    )
                    .mappings()
                    .all()
                )
            if attempts:
                break
            await asyncio.sleep(1)
    if len(attempts) != 1:
        raise E2EBlocked("The fake order has more than one successful primary send")
    attempt = attempts[0]
    messages = await client.get_messages(
        driver.entity, limit=100, offset_date=attempt["completed_at"] + timedelta(seconds=5)
    )
    delivered = select_delivery(
        messages, driver.entity.id, attempt["sending_at"], attempt["completed_at"], expected
    )
    if sku in {"T14_FILE", "T14_DOC"}:
        if (
            delivered.document is None
            or await client.download_media(delivered, bytes) != FAKE_DOCUMENT
        ):
            raise E2EBlocked("The actual document differs from the approved fake fixture")
    if sku == "T14_MANUAL":
        if "T14 FAKE staff action" in delivered.raw_text:
            raise E2EBlocked("Staff instructions leaked into the customer manual notice")
        queue = await driver.command("/manual", "Đơn chờ xử lý thủ công")
        driver.button(queue, f"mf:detail:{order['work_id']}")
        confirm = await driver.command(
            f"/manual_complete {order['work_id']}\n"
            "T14 FAKE completion evidence; no commercial delivery.",
            "Xác nhận hoàn tất công việc",
        )
        await driver.click(confirm, "mf:confirm:", "Đã ghi nhận giao hàng thủ công")
        evidence.passed("delivery.manual_completion", order_id=order["id"])
    current = await read_order(engine, user.id, sku)
    if (
        current is None
        or current["id"] != order["id"]
        or current["status"] != "DELIVERED"
        or current["inventory_unit_id"] != order["inventory_unit_id"]
        or current["resource_id"] != order["resource_id"]
        or current["payment_count"] != 1
    ):
        raise E2EBlocked("Delivery did not preserve the single paid entitlement")
    history = await driver.command(f"/orders {order['id']}", "Đã giao")
    if str(order["id"]) not in history.raw_text:
        raise E2EBlocked("Customer history did not identify the delivered order")
    evidence.passed("delivery." + sku.lower(), order_id=order["id"])
