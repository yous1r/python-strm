from types import SimpleNamespace

from app.config import TelegramConfig
from app.core.monitor.telegram_runtime import extract_message_text, parse_channel_reference


def test_telegram_config_defaults_for_monitor_runtime():
    cfg = TelegramConfig()

    assert cfg.mode == "auto"
    assert cfg.startup_sync == "latest"
    assert cfg.history_limit == 100
    assert cfg.reconnect_backoff == 5


def test_parse_channel_reference_supports_t_me_c_links():
    assert parse_channel_reference("https://t.me/c/123456/99") == -100123456


def test_extract_message_text_prefers_caption_and_text_fields():
    message = SimpleNamespace(message="", text="caption text")
    assert extract_message_text(message) == "caption text"