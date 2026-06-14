from types import SimpleNamespace
from pathlib import Path

from app.config import TelegramConfig
from app.core.monitor.telegram import TelegramMonitor
from app.core.monitor.telegram_runtime import (
    build_telegram_client,
    extract_message_text,
    extract_message_torrent_files,
    parse_channel_reference,
)


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


def test_build_telegram_client_uses_isolated_runtime_session_copy(tmp_path, monkeypatch):
    from app.core.monitor import telegram_runtime

    canonical_base = tmp_path / "data" / "session_strm"
    canonical_file = Path(f"{canonical_base}.session")
    canonical_file.parent.mkdir()
    canonical_file.write_bytes(b"session-db")
    Path(f"{canonical_file}-journal").write_bytes(b"session-journal")

    runtime_dir = tmp_path / "runtime-sessions"
    captured = {}

    class FakeTelegramClient:
        def __init__(self, session_path, api_id, api_hash, **kwargs):
            captured["session_path"] = session_path
            captured["api_id"] = api_id
            captured["api_hash"] = api_hash
            captured["kwargs"] = kwargs

    monkeypatch.setattr(telegram_runtime, "TELEGRAM_RUNTIME_SESSION_DIR", str(runtime_dir), raising=False)
    monkeypatch.setattr(telegram_runtime, "TelegramClient", FakeTelegramClient)

    build_telegram_client("1", "hash", session_path=str(canonical_base))

    assert captured["session_path"] != str(canonical_base)
    assert captured["session_path"].startswith(str(runtime_dir))
    assert Path(f"{captured['session_path']}.session").read_bytes() == b"session-db"
    assert Path(f"{captured['session_path']}.session-journal").read_bytes() == b"session-journal"


def test_build_telegram_client_uses_isolated_runtime_session_when_canonical_missing(tmp_path, monkeypatch):
    from app.core.monitor import telegram_runtime

    canonical_base = tmp_path / "data" / "session_strm"
    runtime_dir = tmp_path / "runtime-sessions"
    captured = {}

    class FakeTelegramClient:
        def __init__(self, session_path, api_id, api_hash, **kwargs):
            captured["session_path"] = session_path

    monkeypatch.setattr(telegram_runtime, "TELEGRAM_RUNTIME_SESSION_DIR", str(runtime_dir), raising=False)
    monkeypatch.setattr(telegram_runtime, "TelegramClient", FakeTelegramClient)

    build_telegram_client("1", "hash", session_path=str(canonical_base))

    assert captured["session_path"] != str(canonical_base)
    assert captured["session_path"].startswith(str(runtime_dir))
    assert not Path(f"{captured['session_path']}.session").exists()


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


def test_extract_message_text_merges_multiple_resource_inputs():
    message = SimpleNamespace(
        text="剧名A\nhttps://115.com/s/abc?password=1234\nmagnet:?xt=urn:btih:DEADBEEF",
        raw_text="剧名A\nhttps://115.com/s/abc?password=1234\nmagnet:?xt=urn:btih:DEADBEEF",
        message="剧名A",
        entities=[SimpleNamespace(url="https://www.123pan.com/s/demo-demo.html?Pwd=qwer")],
        buttons=[[SimpleNamespace(url="https://example.com/detail")]],
        media=SimpleNamespace(webpage=SimpleNamespace(url="https://example.org/fallback")),
    )

    text = extract_message_text(message)
    assert "https://115.com/s/abc?password=1234" in text
    assert "magnet:?xt=urn:btih:DEADBEEF" in text
    assert "https://www.123pan.com/s/demo-demo.html?Pwd=qwer" in text
    assert "https://example.com/detail" in text
    assert "https://example.org/fallback" in text


def test_extract_message_torrent_files_supports_document_attributes():
    message = SimpleNamespace(
        file=None,
        document=SimpleNamespace(
            mime_type="application/x-bittorrent",
            size=2048,
            attributes=[SimpleNamespace(file_name="资源合集.torrent")],
        ),
    )

    assert extract_message_torrent_files(message) == [
        {"name": "资源合集.torrent", "mime_type": "application/x-bittorrent", "size": 2048}
    ]


def test_summarize_resources_filters_non_resource_urls_and_keeps_torrent_files():
    monitor = TelegramMonitor()

    summary = monitor.summarize_resources(
        "剧名B https://115.com/s/abc?password=1234 https://www.123pan.com/s/demo-demo.html?Pwd=qwer magnet:?xt=urn:btih:FACEB00C https://example.com/ref?from=tg",
        torrent_files=[{"name": "剧名B.torrent", "mime_type": "application/x-bittorrent", "size": 128}],
    )

    assert summary is not None
    assert summary["link"] == "https://115.com/s/abc"
    assert summary["disk_type"] == "115"
    assert summary["password"] == "1234"
    assert summary["resource_count"] == 4
    assert summary["magnet_links"] == ["magnet:?xt=urn:btih:FACEB00C"]
    assert summary["url_links"] == [
        "https://115.com/s/abc?password=1234",
        "https://www.123pan.com/s/demo-demo.html?Pwd=qwer",
    ]
    assert summary["torrent_files"][0]["name"] == "剧名B.torrent"
    assert all(item["type"] != "url" for item in summary["resource_links"])
