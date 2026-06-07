from app.events import spawn_task


def apply_runtime_config_changes(changed_data: dict, old_config, new_config) -> None:
    """根据配置差异触发需要的热重载。"""
    if _emby_proxy_changed(changed_data, old_config, new_config):
        from app.core.emby.standalone_proxy import restart_standalone_proxy

        spawn_task(restart_standalone_proxy(), name="proxy_restart")

    if _telegram_monitor_changed(changed_data, old_config, new_config):
        from app.services.telegram_service import restart_monitor

        spawn_task(restart_monitor(), name="tg_restart")


def trigger_sync_task(force: bool = False) -> dict[str, str]:
    from app.core.sync.engine import sync_engine

    spawn_task(sync_engine.run_sync_task(force=force), name="sync_manual")
    message = "强制全自动同步任务已在后台触发" if force else "全自动增量同步任务已在后台触发"
    return {"status": "success", "message": message}


def trigger_db_sync_task() -> dict[str, str]:
    from app.services.cloud115_full_sync_service import cloud115_full_sync_service

    spawn_task(cloud115_full_sync_service.start_full_sync(source="system"), name="db_sync_manual")
    return {"status": "success", "message": "115 全链路同步任务已触发，稍后可在调试页查看结果"}


def trigger_emby_preheat_task(
    *,
    instance_name: str = "",
    user_id: str = "",
    library_ids: list[str] | None = None,
    limit: int = 0,
    overwrite: bool = False,
) -> dict[str, str]:
    from app.core.emby.standalone_proxy import preheat_media_item_links

    spawn_task(
        preheat_media_item_links(
            instance_name=instance_name,
            user_id=user_id,
            library_ids=library_ids,
            limit=limit,
            overwrite=overwrite,
        ),
        name="emby_preheat_media_links",
    )
    return {"status": "success", "message": "Emby media_item_links 预热任务已在后台触发"}


def _emby_proxy_changed(changed_data: dict, old_config, new_config) -> bool:
    if "emby" not in changed_data or "proxy" not in changed_data["emby"]:
        return False

    return old_config.emby.proxy.model_dump() != new_config.emby.proxy.model_dump()


def _telegram_monitor_changed(changed_data: dict, old_config, new_config) -> bool:
    if "monitor" not in changed_data or "telegram" not in changed_data["monitor"]:
        return False

    return old_config.monitor.telegram.model_dump() != new_config.monitor.telegram.model_dump()