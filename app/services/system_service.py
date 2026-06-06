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
    from app.core.cloud115.db_sync import sync_all_configured

    spawn_task(sync_all_configured(), name="db_sync_manual")
    return {"status": "success", "message": "115 目录树同步任务已触发，稍后本地缓存将更新"}


def _emby_proxy_changed(changed_data: dict, old_config, new_config) -> bool:
    if "emby" not in changed_data or "proxy" not in changed_data["emby"]:
        return False

    return old_config.emby.proxy.model_dump() != new_config.emby.proxy.model_dump()


def _telegram_monitor_changed(changed_data: dict, old_config, new_config) -> bool:
    if "monitor" not in changed_data or "telegram" not in changed_data["monitor"]:
        return False

    return old_config.monitor.telegram.model_dump() != new_config.monitor.telegram.model_dump()