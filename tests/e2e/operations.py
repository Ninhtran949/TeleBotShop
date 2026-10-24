"""Owner-side Test Server acceptance for support and one real test refund."""

import asyncio
from uuid import UUID

from sqlalchemy import text

from tests.e2e.checkout import read_order, require_fixture
from tests.e2e.driver import Driver
from tests.e2e.effects import EffectJournal
from tests.e2e.evidence import Evidence
from tests.e2e.safety import E2EBlocked, validate_actor


async def support(config, actor, client, bot, user):
    if actor != "owner":
        raise E2EBlocked("The support staff scenario requires the verified Test Owner")
    entity = await client.get_entity(bot.username)
    if entity.id != bot.id:
        raise E2EBlocked("The user client resolved the wrong Test bot")
    driver, evidence = Driver(client, entity), Evidence(config.root, actor)
    created = await driver.command(
        "/ticket GENERAL\nT14 FAKE buyer support request; no personal data.", "Đã nhận ticket:"
    )
    ticket = UUID(driver.button(created, "sp:view:").data.decode().split(":")[2])
    await driver.click(created, "sp:view:", "T14 FAKE buyer support request")
    assignment = await driver.command(
        f"/ticket_assign {ticket} {user.id}", "Xác nhận phân công lại ticket?"
    )
    await driver.click(assignment, "sp:confirm:", "Đã cập nhật ticket")
    await driver.command(f"/staff_reply {ticket}\nT14 FAKE staff response.", "Đã lưu phản hồi")
    await driver.command(f"/ticket {ticket}", "T14 FAKE staff response.")
    evidence.passed("support.owner_thread")


async def support_pair(config, owner_client, buyer_client, bot, owner, buyer):
    validate_actor(owner, config.bot.owner_id, "owner")
    validate_actor(buyer, config.bot.owner_id, "buyer2")
    owner_entity = await owner_client.get_entity(bot.username)
    buyer_entity = await buyer_client.get_entity(bot.username)
    if any(entity.id != bot.id or not entity.bot for entity in (owner_entity, buyer_entity)):
        raise E2EBlocked("The user clients resolved a different Test bot")
    staff = Driver(owner_client, owner_entity)
    customer = Driver(buyer_client, buyer_entity)
    evidence = Evidence(config.root, "buyer2")
    await customer.command("/start", "Chào mừng bạn")
    created = await customer.command(
        "/ticket GENERAL\nT14 FAKE two-user support request; no personal data.", "Đã nhận ticket:"
    )
    ticket = UUID(customer.button(created, "sp:view:").data.decode().split(":")[2])
    await customer.click(created, "sp:view:", "T14 FAKE two-user support request")
    assignment = await staff.command(
        f"/ticket_assign {ticket} {owner.id}", "Xác nhận phân công lại ticket?"
    )
    await staff.click(assignment, "sp:confirm:", "Đã cập nhật ticket")
    notification = await customer.perform(
        lambda: staff.command(
            f"/staff_reply {ticket}\nT14 FAKE cross-user staff response.", "Đã lưu phản hồi"
        ),
        lambda message: (
            str(ticket) in message.raw_text and "Ticket của bạn có phản hồi" in message.raw_text
        ),
    )
    expected = (
        f"Ticket của bạn có phản hồi; mở thread để đọc\nMã tham chiếu: {ticket}\n/ticket {ticket}"
    )
    if notification.raw_text != expected:
        raise E2EBlocked("The customer notification contains unexpected content")
    await customer.command(f"/ticket {ticket}", "T14 FAKE cross-user staff response.")
    evidence.passed("support.cross_user_thread")
    evidence.passed("support.cross_user_notification")


async def refund(config, actor, client, bot, user, engine, sku):
    if actor != "owner":
        raise E2EBlocked("Refund acceptance requires the verified Test Owner")
    await require_fixture(engine, sku)
    order = await read_order(engine, user.id, sku)
    if (
        order is None
        or order["payment_id"] is None
        or order["payment_count"] != 1
        or order["price_stars"] != 1
        or order["currency"] != "XTR"
    ):
        raise E2EBlocked("A single real matched payment is required before refund acceptance")
    if order["status"] not in {"DELIVERED", "REFUNDED"}:
        raise E2EBlocked("Finish observing the fake delivery before refund acceptance")
    entity = await client.get_entity(bot.username)
    if entity.id != bot.id:
        raise E2EBlocked("The user client resolved the wrong Test bot")
    driver, evidence = Driver(client, entity), Evidence(config.root, actor)
    if order["status"] != "REFUNDED":
        await driver.command(
            f"/verify_charge {order['payment_id']}", "Đã lưu bằng chứng giao dịch khớp"
        )
        confirm = await driver.command(
            f"/refund {order['payment_id']}\nT14 FAKE acceptance refund; no commercial value.",
            "Xác nhận hoàn toàn bộ 1 Stars?",
        )
        journal = EffectJournal(config.root, order["id"], "refund")
        button = driver.button(confirm, "rf:confirm:")
        await asyncio.sleep(2.2)
        journal.begin()
        async with asyncio.timeout(25):
            await button.click()
        async with asyncio.timeout(40):
            while True:
                current = await read_order(engine, user.id, sku)
                if current["status"] == "REFUNDED":
                    break
                await asyncio.sleep(1)
        journal.confirm()
    async with engine.connect() as connection:
        succeeded = await connection.scalar(
            text(
                "SELECT count(*) FROM refund_intents "
                "WHERE payment_id=:payment AND status='SUCCEEDED'"
            ),
            {"payment": order["payment_id"]},
        )
        unit_status = (
            await connection.scalar(
                text("SELECT status FROM inventory_units WHERE id=:unit"),
                {"unit": order["inventory_unit_id"]},
            )
            if order["inventory_unit_id"]
            else None
        )
    current = await read_order(engine, user.id, sku)
    if (
        succeeded != 1
        or current["status"] != "REFUNDED"
        or current["inventory_unit_id"] != order["inventory_unit_id"]
        or current["resource_id"] != order["resource_id"]
        or (order["inventory_unit_id"] is not None and unit_status != "ALLOCATED")
    ):
        raise E2EBlocked("Refund did not preserve the permanent paid entitlement")
    evidence.passed("refund." + sku.lower(), order_id=order["id"])
