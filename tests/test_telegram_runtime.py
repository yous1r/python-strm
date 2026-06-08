from types import SimpleNamespace

from app.config import TelegramConfig
from app.core.monitor.telegram_runtime import extract_message_text, parse_channel_reference


def test_telegram_config_defaults_for_monitor_runtime():
    cfg = TelegramConfig()

    assert cfg.mode == "auto"
    assert cfg.startup_sync == "latest"
    assert cfg.history_limit == 100
    assert cfg.reconnect_backoff == 5


def test_telegram_config_defaults_for_background_sync():
    cfg = TelegramConfig()

    assert cfg.scheduled_sync_enabled is True
    assert cfg.scheduled_sync_interval_minutes == 5
    assert cfg.scheduled_sync_limit == 20
    assert cfg.full_sync_skip_if_scheduled_within_minutes == 20


def test_parse_channel_reference_supports_t_me_c_links():
    assert parse_channel_reference("https://t.me/c/123456/99") == -100123456


def test_extract_message_text_prefers_caption_and_text_fields():
    message = SimpleNamespace(message="", text="caption text")
    assert extract_message_text(message) == "caption text"


def test_extract_message_text_prefers_field_with_share_link():
    message = SimpleNamespace(
        raw_text="纯文本说明\n\n🔗 链接： 点击跳转",
        message="纯文本说明\n\n🔗 链接： 点击跳转",
        text="纯文本说明\n\n🔗 **链接：** [点击跳转](https://115cdn.com/s/sws6cdk3npm?password=8888#)",
    )

    assert "https://115cdn.com/s/sws6cdk3npm?password=8888#" in extract_message_text(message)