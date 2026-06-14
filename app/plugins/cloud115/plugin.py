from __future__ import annotations

from typing import Any

from app.core.cloud.plugin import CloudDrivePlugin
from app.core.cloud115.client import client_115
from app.core.cloud115.db_sync import sync_all_configured, sync_directory
from app.core.cloud115.strm import generator_115


class Cloud115Plugin(CloudDrivePlugin):
    @property
    def cloud_type(self) -> str:
        return "115"

    @property
    def display_name(self) -> str:
        return "115 网盘"

    @property
    def config_key(self) -> str:
        return "cloud115"

    @property
    def client(self):
        return client_115

    @property
    def strm_generator(self):
        return generator_115

    def get_config(self, app_config: Any) -> Any:
        return getattr(app_config, self.config_key)

    def list_sync_dirs(self, app_config: Any) -> list[Any]:
        return list(getattr(self.get_config(app_config), "sync_dirs", []) or [])

    async def sync_directory(self, dir_id: str, dir_name: str = "", recursive: bool = True) -> int:
        return await sync_directory(dir_id, dir_name, recursive)

    async def sync_all_configured(self, app_config=None) -> dict:
        return await sync_all_configured()


cloud115_plugin = Cloud115Plugin()
