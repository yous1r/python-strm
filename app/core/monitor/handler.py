from loguru import logger
from app.events import event_bus, EVENT_MONITOR_NEW_LINK
from app.services.telegram_resource_transfer_service import process_resource_transfer

async def handle_new_link(link_data: dict, source: str, **kwargs):
    result = await process_resource_transfer(link_data, source=source)
    if result.get("status") == "skipped":
        logger.debug(f"Skipping Telegram resource transfer: {result.get('reason')}")

def init_handlers():
    event_bus.subscribe(EVENT_MONITOR_NEW_LINK, handle_new_link)
    logger.info("Registered Telegram link monitor handler.")
