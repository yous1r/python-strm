from app.config import get_config


CLOUD_NAMES = {
    "115": "115 网盘",
    "123": "123 网盘",
}


def normalize_cloud_type(cloud_type: str | None) -> str:
    return str(cloud_type or "115").strip().lower()


def get_cloud_display_name(cloud_type: str | None) -> str:
    normalized = normalize_cloud_type(cloud_type)
    return CLOUD_NAMES.get(normalized, f"{normalized} 网盘")


def list_transfer_destinations(cloud_type: str | None = "115", config=None) -> list[dict[str, str]]:
    config = config or get_config()
    normalized = normalize_cloud_type(cloud_type)
    if normalized == "115":
        sync_dirs = getattr(getattr(config, "cloud115", None), "sync_dirs", []) or []
    elif normalized == "123":
        sync_dirs = getattr(getattr(config, "cloud123", None), "sync_dirs", []) or []
    else:
        sync_dirs = []

    destinations: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sync_dirs:
        if isinstance(item, dict):
            dir_id = str(item.get("dir_id") or "").strip()
            name = str(item.get("name") or item.get("dir_name") or dir_id).strip()
        else:
            dir_id = str(getattr(item, "dir_id", "") or "").strip()
            name = str(getattr(item, "name", "") or getattr(item, "dir_name", "") or dir_id).strip()
        if not dir_id or dir_id in seen:
            continue
        seen.add(dir_id)
        destinations.append({"dir_id": dir_id, "name": name or dir_id})
    return destinations


def resolve_transfer_destination(
    cloud_type: str | None = "115",
    target_dir_id: str | None = "",
    *,
    config=None,
) -> dict[str, str]:
    normalized = normalize_cloud_type(cloud_type)
    destinations = list_transfer_destinations(normalized, config=config)
    selected_id = str(target_dir_id or "").strip()

    if selected_id:
        for destination in destinations:
            if destination["dir_id"] == selected_id:
                return destination
        raise ValueError(f"请选择已配置的 {get_cloud_display_name(normalized)} STRM 扫描源目录")

    if len(destinations) == 1:
        return destinations[0]
    if destinations:
        raise ValueError("请选择转存目的地 STRM 扫描源目录")
    raise ValueError(f"请先在 STRM 配置中添加 {get_cloud_display_name(normalized)}的扫描源目录")
