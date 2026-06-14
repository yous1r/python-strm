from types import SimpleNamespace

import pytest


def test_default_cloud_plugin_registry_exposes_115_plugin():
    from app.core.cloud import cloud_plugin_registry, get_cloud_plugin
    from app.core.cloud.plugin import CloudDrivePlugin

    plugin = get_cloud_plugin("115")

    assert isinstance(plugin, CloudDrivePlugin)
    assert plugin.cloud_type == "115"
    assert plugin.display_name == "115 网盘"
    assert plugin.client is not None
    assert plugin.strm_generator is not None
    assert callable(plugin.sync_directory)
    assert cloud_plugin_registry.normalize_cloud_type(" 115 ") == "115"


def test_cloud_plugin_registry_rejects_unknown_plugin():
    from app.core.cloud import get_cloud_plugin
    from app.core.cloud.plugin import CloudPluginNotFoundError

    with pytest.raises(CloudPluginNotFoundError):
        get_cloud_plugin("unknown")


def test_transfer_destinations_are_resolved_from_cloud_plugin(monkeypatch):
    from app.services.transfer_destination_service import (
        get_cloud_display_name,
        list_transfer_destinations,
        resolve_transfer_destination,
    )

    config = SimpleNamespace(
        cloud115=SimpleNamespace(
            sync_dirs=[
                SimpleNamespace(dir_id="cid-a", name="115 STRM 扫描源目录"),
                SimpleNamespace(dir_id="cid-a", name="重复目录"),
                SimpleNamespace(dir_id="cid-b", name="115 动漫扫描源"),
            ]
        )
    )

    destinations = list_transfer_destinations("115", config=config)

    assert get_cloud_display_name("115") == "115 网盘"
    assert destinations == [
        {"dir_id": "cid-a", "name": "115 STRM 扫描源目录"},
        {"dir_id": "cid-b", "name": "115 动漫扫描源"},
    ]
    assert resolve_transfer_destination("115", "cid-b", config=config) == {
        "dir_id": "cid-b",
        "name": "115 动漫扫描源",
    }


def test_transfer_destination_unknown_cloud_uses_plugin_error():
    from app.core.cloud.plugin import CloudPluginNotFoundError
    from app.services.transfer_destination_service import list_transfer_destinations

    with pytest.raises(CloudPluginNotFoundError):
        list_transfer_destinations("123")


def test_app_config_hydrates_115_plugin_config_from_clouds_container():
    from app.config import _build_app_config

    config = _build_app_config(
        {
            "clouds": {
                "plugins": {
                    "115": {
                        "enabled": True,
                        "cookie": "UID=demo",
                        "sync_dirs": [{"dir_id": "cid-1", "name": "115 STRM 扫描源目录"}],
                    }
                }
            }
        }
    )

    assert config.cloud115.enabled is True
    assert config.cloud115.cookie == "UID=demo"
    assert config.cloud115.sync_dirs[0].dir_id == "cid-1"
    assert config.clouds.plugins["115"]["sync_dirs"][0]["name"] == "115 STRM 扫描源目录"


def test_app_config_keeps_legacy_cloud115_config_as_default_plugin_config():
    from app.config import _build_app_config

    config = _build_app_config(
        {
            "cloud115": {
                "enabled": True,
                "cookie": "UID=legacy",
                "sync_dirs": [{"dir_id": "legacy-cid", "name": "legacy scan"}],
            }
        }
    )

    assert config.cloud115.cookie == "UID=legacy"
    assert config.clouds.plugins["115"]["cookie"] == "UID=legacy"
    assert config.clouds.plugins["115"]["sync_dirs"][0]["dir_id"] == "legacy-cid"
