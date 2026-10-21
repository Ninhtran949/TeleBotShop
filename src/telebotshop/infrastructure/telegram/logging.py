import logging


class TelegramLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # SDK exception strings may contain response bodies or token-bearing request URLs.
        record.msg = "telegram_sdk_" + record.levelname.lower()
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_telegram_logging() -> None:
    for name in (
        "aiogram.dispatcher",
        "aiogram.event",
        "aiogram.middlewares",
        "aiogram.webhook",
        "aiogram.scene",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(item, TelegramLogFilter) for item in logger.filters):
            logger.addFilter(TelegramLogFilter())
