import pytest

from app.database import (
    get_db_conn,
    get_telegram_monitor_state,
    init_db,
    insert_tg_resource,
    list_telegram_monitor_states,
    upsert_telegram_monitor_state,
)


@pytest.mark.asyncio
async def test_insert_tg_resource_is_unique_per_channel_message_link(tmp_path):
    from app.config import load_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"database:\n  path: '{tmp_path / 'test.db'}'\n", encoding="utf-8")
    load_config(str(config_file))
    await init_db()

    resource = {
        "message_id": 11,
        "channel_id": "-1001",
        "title": "demo",
        "raw_text": "https://115.com/s/abc",
        "link": "https://115.com/s/abc",
        "password": "",
        "disk_type": "115",
    }

    async with get_db_conn() as db:
        assert await insert_tg_resource(db, resource) is True
        assert await insert_tg_resource(db, resource) is False
        await db.commit()


@pytest.mark.asyncio
async def test_insert_tg_resource_is_unique_per_channel_message_even_with_different_link(tmp_path):
    from app.config import load_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"database:\n  path: '{tmp_path / 'test.db'}'\n", encoding="utf-8")
    load_config(str(config_file))
    await init_db()

    first = {
        "message_id": 11,
        "channel_id": "-1001",
        "title": "demo",
        "raw_text": "https://115.com/s/abc",
        "link": "https://115.com/s/abc",
        "password": "",
        "disk_type": "115",
    }
    second = {
        **first,
        "link": "https://www.123pan.com/s/demo-demo.html",
        "disk_type": "123",
    }

    async with get_db_conn() as db:
        assert await insert_tg_resource(db, first) is True
        assert await insert_tg_resource(db, second) is False
        await db.commit()


@pytest.mark.asyncio
async def test_upsert_and_get_monitor_state(tmp_path):
    from app.config import load_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"database:\n  path: '{tmp_path / 'test.db'}'\n", encoding="utf-8")
    load_config(str(config_file))
    await init_db()

    await upsert_telegram_monitor_state(
        channel_ref="@demo",
        resolved_channel_id="-100123",
        last_message_id=99,
        last_message_date="2026-06-06T10:00:00",
        last_error="",
    )

    state = await get_telegram_monitor_state("@demo")
    assert state["last_message_id"] == 99
    assert state["resolved_channel_id"] == "-100123"

    states = await list_telegram_monitor_states()
    assert states[0]["channel_ref"] == "@demo"