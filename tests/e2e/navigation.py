"""Real user commands and callbacks against the running Test bot."""

from tests.e2e.driver import Driver
from tests.e2e.evidence import Evidence
from tests.e2e.safety import E2EBlocked


async def navigation(config, actor, client, bot, user):
    entity = await client.get_entity(bot.username)
    if entity.id != bot.id or not entity.bot:
        raise E2EBlocked("The user client resolved a different Test bot")
    driver, evidence = Driver(client, entity), Evidence(config.root, actor)
    home = await driver.command("/start", "Chào mừng bạn")
    evidence.passed("navigation.start")
    profile = await driver.click(home, "profile", "Tài khoản của bạn")
    if str(user.id) not in profile.raw_text:
        raise E2EBlocked("The profile did not identify the current Test user")
    evidence.passed("navigation.profile")
    home = await driver.click(profile, "home", "Chào mừng bạn")
    shop = await driver.click(home, "shop:root", "Cửa hàng")
    category = await driver.click(shop, "shop:c:T14:", "Trang 1")
    products = await driver.click(category, "shop:a:", "Sản phẩm")
    await driver.click(products, "home", "Chào mừng bạn")
    evidence.passed("navigation.browse_home")
    search = await driver.command("/search T14 FAKE KEY", "Kết quả cho:")
    detail = await driver.click(search, "shop:p:", "SKU: T14_KEY")
    if "Giá: 1 Stars" not in detail.raw_text:
        raise E2EBlocked("The fake fixture price differs from one test Star")
    await driver.click(detail, "shop:v:", "Kết quả cho:")
    evidence.passed("navigation.search_detail_back")
    for command, expected in (
        ("terms", "Điều khoản cửa hàng"),
        ("orders", "Đơn hàng của bạn"),
        ("support", "Trung tâm hỗ trợ"),
        ("paysupport", "Hỗ trợ thanh toán Stars"),
        ("tickets", "Yêu cầu hỗ trợ của bạn"),
    ):
        await driver.command("/" + command, expected)
        evidence.passed("navigation." + command)
    if actor == "owner":
        admin = await driver.command("/admin", "Quản trị")
        for prefix in ("pc:list", "iv:home", "ord:a", "ord:k", "st:list", "rf:list", "mf:queue"):
            driver.button(admin, prefix)
        evidence.passed("navigation.admin_menu")
        customer = await driver.command(f"/customer {user.id}", "Khách mua (Telegram ID):")
        if f"Khách mua (Telegram ID): {user.id}" not in customer.raw_text:
            raise E2EBlocked("The customer lookup did not identify the selected Test user")
        evidence.passed("navigation.customer_lookup")
        for name, prefix, expected in (
            ("products", "pc:list:", "Sản phẩm · Trang"),
            ("inventory", "iv:home", "Quản lý kho"),
            ("resources", "rs:home", "Nguồn giao hàng"),
            ("dashboard", "ord:d:", "Tổng quan đơn"),
            ("staff_orders", "ord:a:", "Đơn shop"),
            ("staff_tickets", "st:list:", "Ticket đang mở"),
            ("refunds", "rf:list:", "Thanh toán / hoàn tiền"),
            ("manual", "mf:queue:", "Đơn chờ xử lý thủ công"),
            ("access", "adm:access", "Quản lý quyền"),
            ("settings", "adm:settings", "Cài đặt"),
        ):
            admin = await driver.command("/admin", "Quản trị")
            await driver.click(admin, prefix, expected)
            evidence.passed("navigation.admin_" + name)
    else:
        await driver.command("/admin", "Bạn không có quyền")
        evidence.passed("navigation.admin_default_deny")
