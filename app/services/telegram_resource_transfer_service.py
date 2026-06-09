import asyncio

from loguru import logger

import uuid

from app.config import get_config
from app.core.cloud115.client import client_115
from app.core.cloud115.strm import generator_115
from app.core.notify.manager import notify_manager
from app.core.transfer.classifier import classify
from app.core.transfer.placement import derive_series_scope_path
from app.core.transfer.receive_target import infer_archive_rel_path, prepare_receive_target
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

    config = get_config()
    monitor_cfg = config.monitor.telegram
    transfer_cfg = config.transfer
    archive_dir_id = transfer_cfg.archive_dir_id
    target_dir_id = link_data.get("series_folder_id") or archive_dir_id

    share_url = link_data.get("url")
    receive_code = link_data.get("password", "")
    db_id = link_data.get("db_id")

    if db_id:
        await _update_tg_status(db_id, "queued")

    if not client_115.client:
        await _update_tg_status(db_id, "failed")
        return {"status": "failed", "error": "115 client not initialized", "resource": link_data, "source": source}
    if not archive_dir_id or archive_dir_id == "0":
        await _update_tg_status(db_id, "failed")
        return {"status": "failed", "error": "archive_dir_id 未配置", "resource": link_data, "source": source}

    try:
        async with transfer_semaphore:
            logger.info(f"Processing Telegram 115 link: {share_url} source={source}")
            filter_rules = None if link_data.get("ignore_filters") else monitor_cfg.filter_rules
            receive_target = None
            if not link_data.get("series_folder_id") and archive_dir_id and archive_dir_id != "0":
                receive_target = await prepare_receive_target(
                    share_url=share_url,
                    receive_code=receive_code,
                    archive_dir_id=archive_dir_id,
                    fallback_dir_id=archive_dir_id,
                    classifier=classify,
                )
                if receive_target.target_dir_id:
                    target_dir_id = receive_target.target_dir_id

            if not target_dir_id or target_dir_id == "0":
                await _update_tg_status(db_id, "failed")
                return {"status": "failed", "error": "target_dir_id 未配置", "resource": link_data, "source": source}

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
        share_files = transfer_res.get("share_files") or []
        archive_rel_path = receive_target.archive_rel_path if receive_target else ""
        if not archive_rel_path and link_data.get("series_folder_id") and share_files:
            archive_rel_path = await infer_archive_rel_path(share_files, classifier=classify)
        if share_files and archive_rel_path:
            await generator_115.generate_strm_for_folder(
                target_dir_id,
                share_files,
                archive_rel_path,
                config.strm.output_dir,
                task_id=str(uuid.uuid4()),
            )
            await generator_115.sync_strm_files_from_manifest(
                dir_id=target_dir_id,
                output_dir=config.strm.output_dir,
                root_output_dir=config.strm.output_dir,
                base_url=getattr(config.strm, "base_url", "") or "",
                archive_root=derive_series_scope_path(archive_rel_path),
            )

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

