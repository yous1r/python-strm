from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable


class CloudPluginError(Exception):
    """Base exception for cloud drive plugin errors."""


class CloudPluginNotFoundError(CloudPluginError):
    """Raised when a cloud drive plugin is not registered."""


@runtime_checkable
class CloudClientProtocol(Protocol):
    @property
    def client(self) -> Any: ...

    async def list_files(self, dir_id: str = "0", limit: int = 100, offset: int = 0) -> dict: ...

    async def list_dirs(self, dir_id: str = "0") -> dict: ...

    async def create_folder(self, parent_id: str, name: str) -> dict: ...

    async def create_path(self, parent_id: str, path: str) -> dict: ...

    async def rename_file(self, file_id: str, new_name: str) -> bool: ...

    async def move_files(self, file_ids: list[str], target_dir_id: str) -> bool: ...

    async def share_receive(
        self,
        share_url: str,
        receive_code: str = "",
        target_dir_id: str = "0",
        filter_rules: list[str] | None = None,
    ) -> dict: ...

    async def get_share_info(self, share_url: str, receive_code: str = "") -> dict: ...

    async def get_download_url(self, pickcode: str, user_agent: str | None = None) -> str: ...

    async def offline_add_url(self, url: str, target_dir_id: str) -> dict: ...

    async def get_offline_tasks(self) -> list[dict]: ...


@runtime_checkable
class CloudStrmGeneratorProtocol(Protocol):
    async def batch_generate(self, *args, **kwargs) -> list: ...

    async def sync_manifest_records(self, *args, **kwargs) -> dict: ...

    async def sync_strm_files_from_manifest(self, *args, **kwargs) -> dict: ...

    async def generate_strm_for_folder(self, *args, **kwargs) -> list: ...

    async def rewrite_manifest_records(self, *args, **kwargs) -> list: ...

    async def rewrite_from_manifest(self, *args, **kwargs) -> dict: ...


class CloudDrivePlugin(ABC):
    """Contract implemented by built-in and future cloud drive plugins."""

    @property
    @abstractmethod
    def cloud_type(self) -> str:
        """Stable cloud type used in config, DB records, and URLs."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable cloud drive name."""

    @property
    @abstractmethod
    def config_key(self) -> str:
        """AppConfig attribute containing this plugin's config."""

    @property
    @abstractmethod
    def client(self) -> CloudClientProtocol:
        """Cloud API client used by services."""

    @property
    @abstractmethod
    def strm_generator(self) -> CloudStrmGeneratorProtocol:
        """STRM generator used by services."""

    @abstractmethod
    def get_config(self, app_config: Any) -> Any:
        """Return the plugin-specific config object from AppConfig."""

    @abstractmethod
    def list_sync_dirs(self, app_config: Any) -> list[Any]:
        """Return configured STRM scan source directories."""

    @abstractmethod
    async def sync_directory(self, dir_id: str, dir_name: str = "", recursive: bool = True) -> int:
        """Sync a cloud directory into the local cache."""

    async def sync_all_configured(self, app_config: Any | None = None) -> dict:
        """Sync all configured directories. Plugins may override this."""
        if app_config is None:
            raise NotImplementedError("sync_all_configured requires app_config or plugin override")
        results = {}
        for item in self.list_sync_dirs(app_config):
            dir_id = str(getattr(item, "dir_id", "") or "")
            name = str(getattr(item, "name", "") or dir_id)
            results[name] = await self.sync_directory(dir_id, name, recursive=True)
        return results


class CloudPluginRegistry:
    def __init__(self):
        self._plugins: dict[str, CloudDrivePlugin] = {}

    def normalize_cloud_type(self, cloud_type: str | None) -> str:
        return str(cloud_type or "115").strip().lower()

    def register(self, plugin: CloudDrivePlugin) -> CloudDrivePlugin:
        normalized = self.normalize_cloud_type(plugin.cloud_type)
        self._plugins[normalized] = plugin
        return plugin

    def get(self, cloud_type: str | None = "115") -> CloudDrivePlugin:
        normalized = self.normalize_cloud_type(cloud_type)
        try:
            return self._plugins[normalized]
        except KeyError as exc:
            raise CloudPluginNotFoundError(f"未注册网盘插件: {normalized}") from exc

    def all(self) -> list[CloudDrivePlugin]:
        return list(self._plugins.values())
