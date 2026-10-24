"""One fake purchase through real user callbacks and Telegram's Stars protocol."""

import asyncio

from sqlalchemy import text

from tests.e2e.driver import Driver
from tests.e2e.effects import EffectJournal
from tests.e2e.evidence import Evidence
from tests.e2e.safety import FIXTURES, E2EBlocked, validate_invoice


def select_invoice(messages, bot_id, order_id):
    matches = [
        message
        for message in messages
        if message.sender_id == bot_id
        and getattr(message.media, "start_param", None) == "order_" + order_id.hex
    ]
    if len(matches) != 1:
        raise E2EBlocked("Expected exactly one buyer-side invoice for the owned order")
    return matches[0]


async def read_order(engine, actor_id, sku):
    async with engine.connect() as connection:
        return (
            (
                await connection.execute(
                    text(
                        "SELECT o.id,o.status,o.attention_reason,"
                        "o.expires_at>clock_timestamp() AS live,"
                        "oi.price_stars,oi.currency,i.status AS invoice_status,"
                        "i.message_id AS invoice_message_id,"
                        "p.id AS payment_id,f.id AS work_id,f.state AS delivery_state,"
                        "f.inventory_unit_id,f.resource_id,"
                        "(SELECT count(*) FROM payments p2 WHERE p2.order_id=o.id) "
                        "AS payment_count,"
                        "(SELECT count(*) FROM pre_checkout_approvals a WHERE a.order_id=o.id) "
                        "AS approval_count "
                        "FROM orders o JOIN order_items oi ON oi.order_id=o.id "
                        "JOIN users u ON u.id=o.buyer_id JOIN products pr ON pr.id=oi.product_id "
                        "JOIN invoice_intents i ON i.order_id=o.id "
                        "LEFT JOIN payments p ON p.order_id=o.id AND p.status='MATCHED' "
                        "LEFT JOIN fulfillments f ON f.order_id=o.id "
                        "WHERE u.telegram_id=:actor AND pr.sku=:sku AND o.status<>'EXPIRED' "
                        "ORDER BY o.created_at DESC LIMIT 1"
                    ),
                    {"actor": actor_id, "sku": sku},
                )
            )
            .mappings()
            .one_or_none()
        )


async def require_fixture(engine, sku):
    if sku not in FIXTURES:
        raise E2EBlocked("Only named T14 fake fixtures may be purchased")
    async with engine.connect() as connection:
        product = (
            (
                await connection.execute(
                    text(
                        "SELECT product_type,price_stars,name,description,sale_state "
                        "FROM products WHERE sku=:sku"
                    ),
                    {"sku": sku},
                )
            )
            .mappings()
            .one_or_none()
        )
    if (
        product is None
        or product["product_type"] != FIXTURES[sku]
        or product["price_stars"] != 1
        or product["sale_state"] != "ACTIVE"
        or product["name"] != "T14 FAKE " + sku.removeprefix("T14_")
        or product["description"] != "Internal Test Server fixture. No commercial value."
    ):
        raise E2EBlocked("The fake fixture identity, description, type or price changed")


async def purchase(config, actor, client, bot, user, engine, sku):
    await require_fixture(engine, sku)
    order = await read_order(engine, user.id, sku)
    if order is None:
        async with engine.connect() as connection:
            previous = (
                (
                    await connection.execute(
                        text(
                            "SELECT o.id FROM orders o JOIN order_items oi ON oi.order_id=o.id "
                            "JOIN users u ON u.id=o.buyer_id "
                            "JOIN products pr ON pr.id=oi.product_id "
                            "WHERE u.telegram_id=:actor AND pr.sku=:sku"
                        ),
                        {"actor": user.id, "sku": sku},
                    )
                )
                .scalars()
                .all()
            )
        if any(
            EffectJournal(config.root, order_id, "payment").path.exists() for order_id in previous
        ):
            raise E2EBlocked("An earlier payment attempt for this fixture requires reconciliation")

    from telethon import functions, types

    entity = await client.get_entity(bot.username)
    if entity.id != bot.id:
        raise E2EBlocked("The user client resolved the wrong Test bot")
    driver, evidence = Driver(client, entity), Evidence(config.root, actor)
    if order is None:
        search = await driver.command(
            "/search " + "T14 FAKE " + sku.removeprefix("T14_"), "Kết quả cho:"
        )
        detail = await driver.click(search, "shop:p:", "SKU: " + sku)
        await driver.click(detail, "buy:", "Đơn:")
        order = await read_order(engine, user.id, sku)
    if order is None or order["price_stars"] != 1 or order["currency"] != "XTR":
        raise E2EBlocked("The owned order snapshot does not match the fake fixture")
    if order["status"] == "REFUNDED":
        raise E2EBlocked("This fixture was already refunded; do not create another test purchase")
    if order["payment_id"] is None:
        if order["status"] != "AWAITING_PAYMENT" or not order["live"]:
            raise E2EBlocked("Close expired unpaid orders with the existing expiry use case first")
        if order["invoice_status"] == "READY":
            view = await driver.command(f"/checkout {order['id']}", "Đơn:")
            await driver.click(view, "ck:p:", "Hóa đơn Stars đã gửi")
            order = await read_order(engine, user.id, sku)
        if order["invoice_status"] != "SENT" or order["invoice_message_id"] is None:
            raise E2EBlocked("The invoice send is not confirmed; do not send another invoice")
        message = select_invoice(
            await client.get_messages(entity, limit=100), entity.id, order["id"]
        )
        if message is None or not isinstance(message.media, types.MessageMediaInvoice):
            raise E2EBlocked("The persisted invoice does not identify an actual Telegram invoice")
        invoice = types.InputInvoiceMessage(await client.get_input_entity(entity), message.id)
        form = await client(functions.payments.GetPaymentFormRequest(invoice))
        validate_invoice(message.media, form, entity.id)
        journal = EffectJournal(config.root, order["id"], "payment")
        journal.begin()
        result = await client(functions.payments.SendStarsFormRequest(form.form_id, invoice))
        if not isinstance(result, types.payments.PaymentResult):
            raise E2EBlocked("The Test payment requires reconciliation or external verification")
        async with asyncio.timeout(40):
            while True:
                order = await read_order(engine, user.id, sku)
                if order is not None and order["payment_id"] is not None:
                    break
                await asyncio.sleep(1)
        journal.confirm()
    if (
        order["payment_count"] != 1
        or order["approval_count"] != 1
        or order["attention_reason"] is not None
        or order["work_id"] is None
    ):
        raise E2EBlocked("Receipt/pre-checkout/allocation evidence is incomplete or ambiguous")
    evidence.passed("checkout." + sku.lower(), order_id=order["id"])
    return driver, evidence, order
