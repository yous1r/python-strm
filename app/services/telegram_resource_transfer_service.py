import asyncio
import json
import re

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

_SUPPORTED_LINK_PRIORITY = {"115": 0, "123": 1, "magnet": 2}


def _clean_resource_url(url: str) -> str:
    return (url or "").strip().rstrip('.,);!>')


def _detect_resource_type(url: str) -> str:
    normalized_url = _clean_resource_url(url)
    if re.match(r'^https?://115(?:cdn)?\.com/s/\w+(?:\?[^\s"\'<>]+)?$', normalized_url, re.IGNORECASE):
        return "115"
    if re.match(r'^https?://(?:www\.)?123pan\.com/s/\w+-\w+\.html(?:\?[^\s"\'<>]+)?$', normalized_url, re.IGNORECASE):
        return "123"
    if normalized_url.lower().startswith("magnet:?"):
        return "magnet"
    return ""


def _normalize_resource_link_item(item: dict | None) -> dict | None:
    candidate = dict(item or {})
    raw_candidates = [candidate.get("raw_url"), candidate.get("url"), candidate.get("link")]
    normalized_raw_url = ""
    resolved_type = ""

    for raw_url in raw_candidates:
        normalized = _clean_resource_url(raw_url)
        detected_type = _detect_resource_type(normalized)
        if detected_type:
            normalized_raw_url = normalized
            resolved_type = detected_type
            break

    if not resolved_type:
        return None

    clean_url = normalized_raw_url
    password = str(candidate.get("password") or "")
    if resolved_type == "115":
        pwd_match = re.search(r'password=([a-zA-Z0-9]+)', normalized_raw_url)
        if pwd_match:
            password = pwd_match.group(1)
        clean_url = normalized_raw_url.split('?', 1)[0]
    elif resolved_type == "123":
        pwd_match = re.search(r'Pwd=([a-zA-Z0-9]+)', normalized_raw_url, re.IGNORECASE)
        if pwd_match:
            password = pwd_match.group(1)
        clean_url = normalized_raw_url.split('?', 1)[0]
    else:
        password = ""

    return {
        "url": clean_url,
        "raw_url": normalized_raw_url,
        "password": password,
        "type": resolved_type,
    }


def _normalize_resource_links(resource_links: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in resource_links or []:
        normalized_item = _normalize_resource_link_item(item)
        if not normalized_item:
            continue
        key = (
            normalized_item.get("type", ""),
            normalized_item.get("url", ""),
            normalized_item.get("raw_url", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_item)
    return normalized


def _normalize_torrent_files(torrent_files: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple[str, str, int | None]] = set()
    for torrent_file in torrent_files or []:
        item = {
            "name": (torrent_file.get("name") or "unknown.torrent").strip(),
            "mime_type": (torrent_file.get("mime_type") or "").strip().lower(),
            "size": torrent_file.get("size"),
        }
        key = (item["name"], item["mime_type"], item["size"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized


def _pick_preferred_link(resource_links: list[dict]) -> dict | None:
    if not resource_links:
        return None
    return min(resource_links, key=lambda item: _SUPPORTED_LINK_PRIORITY.get(item.get("type", ""), 999))


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
    for field in ("resource_links", "url_links", "magnet_links", "torrent_files"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            try:
                payload[field] = json.loads(value)
            except json.JSONDecodeError:
                pass

    resource_links = _normalize_resource_links(payload.get("resource_links"))
    fallback_link = _normalize_resource_link_item(payload)
    if fallback_link and not any(
        item.get("type") == fallback_link.get("type") and item.get("url") == fallback_link.get("url")
        for item in resource_links
    ):
        resource_links.append(fallback_link)

    torrent_files = _normalize_torrent_files(payload.get("torrent_files"))
    preferred_link = _pick_preferred_link(resource_links)

    payload["resource_links"] = resource_links
    payload["url_links"] = [item["raw_url"] for item in resource_links if item.get("type") in {"115", "123"}]
    payload["magnet_links"] = [item["url"] for item in resource_links if item.get("type") == "magnet"]
    payload["torrent_files"] = torrent_files

    if preferred_link:
        payload["url"] = preferred_link.get("url", "")
        payload["link"] = preferred_link.get("url", "")
        payload["password"] = preferred_link.get("password", "")
        payload["type"] = preferred_link.get("type", "")
        payload["disk_type"] = preferred_link.get("type", "")
    elif torrent_files:
        first_torrent = torrent_files[0]
        payload["url"] = first_torrent.get("name", "unknown.torrent")
        payload["link"] = payload["url"]
        payload["password"] = ""
        payload["type"] = "torrent"
        payload["disk_type"] = "torrent"
    elif not _detect_resource_type(payload.get("url") or payload.get("link") or ""):
        payload["url"] = ""
        payload["link"] = ""
        payload["password"] = ""

    payload["resource_count"] = len(resource_links) + len(torrent_files)
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

