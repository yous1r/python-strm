from app.core.cloud.plugin import (
    CloudDrivePlugin,
    CloudPluginNotFoundError,
    CloudPluginRegistry,
)
from app.plugins.cloud115.plugin import cloud115_plugin


cloud_plugin_registry = CloudPluginRegistry()
cloud_plugin_registry.register(cloud115_plugin)


def get_cloud_plugin(cloud_type: str | None = "115") -> CloudDrivePlugin:
    return cloud_plugin_registry.get(cloud_type)


def normalize_cloud_type(cloud_type: str | None = "115") -> str:
    return cloud_plugin_registry.normalize_cloud_type(cloud_type)


__all__ = [
    "CloudDrivePlugin",
    "CloudPluginNotFoundError",
    "CloudPluginRegistry",
    "cloud_plugin_registry",
    "get_cloud_plugin",
    "normalize_cloud_type",
]
