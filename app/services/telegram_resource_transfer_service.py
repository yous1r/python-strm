import asyncio

from loguru import logger

from app.config import get_config
from app.core.cloud115.client import client_115
from app.core.notify.manager import notify_manager
from app.database import get_db_conn


transfer_semaphore = asyncio.Semaphore(1)


def normalize_resource_payload(resource: dict | None) -> dict:
    payload = dict(resource or {})
    if payload.get("db_id") is None and payload.get("id") is not None:
        payload["db_id"] = payload.get("id")
    if not payload.get("url") and payload.get("link"):
        payload["url"] = payload.get("link")
    if not payload.get("link") and payload.get("url"):
        payload["link"] = payload.get("url")
    if not payload.get("type") and payload.get("disk_type"):
        payload["type"] = payload.get("disk_type")
    if not payload.get("disk_type") and payload.get("type"):
        payload["disk_type"] = payload.get("type")
    payload.setdefault("status", "pending")
    return payload


async def process_resource_transfer(resource: dict, *, source: str = "telegram") -> dict[str, object]:
    link_data = normalize_resource_payload(resource)
    if link_data.get("type") != "115":
        return {"status": "skipped", "reason": "unsupported_disk_type", "resource": link_data, "source": source}

    monitor_cfg = get_config().monitor.telegram
    transfer_cfg = get_config().transfer
    archive_dir_id = monitor_cfg.archive_dir_id
    target_dir_id = link_data.get("series_folder_id") or transfer_cfg.temp_dir_id or monitor_cfg.target_dir_id
    if not target_dir_id or target_dir_id == "0":
        target_dir_id = get_config().cloud115.target_dir_id

    share_url = link_data.get("url")
    receive_code = link_data.get("password", "")
    db_id = link_data.get("db_id")

    if db_id:
        await _update_tg_status(db_id, "queued")

    if not target_dir_id or target_dir_id == "0":
        await _update_tg_status(db_id, "failed")
        return {"status": "failed", "error": "target_dir_id 未配置", "resource": link_data, "source": source}

    if not client_115.client:
        await _update_tg_status(db_id, "failed")
        return {"status": "failed", "error": "115 client not initialized", "resource": link_data, "source": source}

    try:
        async with transfer_semaphore:
            logger.info(f"Processing Telegram 115 link: {share_url} source={source}")
            filter_rules = None if link_data.get("ignore_filters") else monitor_cfg.filter_rules
            transfer_res = await client_115.share_receive(
                share_url,
                receive_code,
                target_dir_id,
                filter_rules=filter_rules,
            )
            await asyncio.sleep(3)

        if not transfer_res.get("state"):
            error_message = transfer_res.get("error") or "转存失败"
            logger.error(f"Failed to auto-transfer link {share_url}: {error_message}")
            await _notify_transfer_failure(share_url, error_message)
            await _update_tg_status(db_id, "failed")
            return {
                "status": "failed",
                "error": error_message,
                "resource": link_data,
                "target_dir_id": target_dir_id,
                "source": source,
            }

        logger.info(f"Successfully transferred {share_url}")
        if monitor_cfg.auto_organize and archive_dir_id and archive_dir_id != "0":
            await _auto_organize(target_dir_id, archive_dir_id)

        await _notify_transfer_success(share_url, receive_code)
        await _update_tg_status(db_id, "success")
        return {
            "status": "success",
            "resource": link_data,
            "share_files": transfer_res.get("share_files") or [],
            "target_dir_id": target_dir_id,
            "source": source,
        }
    except Exception as exc:
        logger.error(f"Exception during Telegram transfer: {exc}")
        await _update_tg_status(db_id, "failed")
        return {
            "status": "failed",
            "error": str(exc),
            "resource": link_data,
            "target_dir_id": target_dir_id,
            "source": source,
        }


async def _notify_transfer_success(share_url: str, receive_code: str):
    await notify_manager.notify(
        title="[STRM] 自动转存成功",
        content=f"链接: {share_url}\n密码: {receive_code}\n已成功转存并加入处理队列！",
        message_type="success",
        group="transfer-single",
    )


async def _notify_transfer_failure(share_url: str, error_message: str):
    await notify_manager.notify(
        title="[STRM] 自动转存失败",
        content=f"链接: {share_url}\n报错: {error_message}",
        message_type="error",
        group="transfer-single",
    )


async def _update_tg_status(db_id, status: str):
    if not db_id:
        return
    async with get_db_conn() as db:
        await db.execute("UPDATE tg_resources SET status = ? WHERE id = ?", (status, db_id))
        await db.commit()


async def _auto_organize(source_dir_id: str, archive_dir_id: str):
    try:
        files_res = await client_115.list_files(source_dir_id, limit=100)
        if files_res.get("error"):
            logger.error("Failed to list files for auto-organize.")
            return

        for item in files_res.get("items", []):
            if item.get("is_dir"):
                sub_res = await client_115.list_files(item["cid"], limit=100)
                sub_items = sub_res.get("items", []) if not sub_res.get("error") else []
                for sub_item in sub_items:
                    if not sub_item.get("is_dir"):
                        await _process_single_file(sub_item, archive_dir_id)
            else:
                await _process_single_file(item, archive_dir_id)
    except Exception as exc:
        logger.error(f"Error during auto_organize: {exc}")


async def _process_single_file(file_item: dict, base_archive_id: str):
    from app.core.media.organizer import organizer

    file_name = file_item.get("n", "")
    file_id = file_item.get("fid", "")
    try:
        category, region, target_folder, target_name, _ = await organizer.get_organized_path(file_name)
    except Exception as exc:
        logger.error(f"Failed to organize file {file_name}: {exc}")
        return

    current_pid = base_archive_id
    path_parts = [category, region] + target_folder.split("/")
    for part in path_parts:
        if not part:
            continue
        mkdir_res = await client_115.create_folder(current_pid, part)
        if "id" in mkdir_res:
            current_pid = mkdir_res["id"]
            continue
        dirs_res = await client_115.list_dirs(current_pid)
        for directory in dirs_res.get("dirs", []):
            if directory.get("n") == part:
                current_pid = directory.get("cid")
                break
        else:
            logger.error(f"Failed to create or find folder {part}")
            return

    move_ok = await client_115.move_files([file_id], current_pid)
    if not move_ok:
        logger.error(f"Failed to move file {file_name} to {current_pid}")
        return
    if target_name != file_name:
        await client_115.rename_file(file_id, target_name)
    logger.info(f"Organized file {file_name} -> {target_folder}/{target_name}")