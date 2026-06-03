import asyncio
from loguru import logger
from app.events import event_bus, EVENT_MONITOR_NEW_LINK, EVENT_TRANSFER_RECEIVED
from app.config import get_config
from app.core.cloud115.client import client_115
from app.core.notify.manager import notify_manager
from app.core.sync.engine import sync_engine

# 限制并发转存数量为1，避免短时间内大量触发115接口导致封控
transfer_semaphore = asyncio.Semaphore(1)

async def handle_new_link(link_data: dict, source: str, **kwargs):
    """
    Handle new links from Telegram or other monitors.
    link_data format: {"url": "https://115.com/s/xxxx", "password": "xxxx", "type": "115"}
    """
    if link_data.get("type") != "115":
        logger.debug(f"Ignoring non-115 link: {link_data}")
        return

    monitor_cfg = get_config().monitor.telegram
    target_dir_id = monitor_cfg.target_dir_id
    archive_dir_id = monitor_cfg.archive_dir_id
    
    # 回退机制：如果监控配置中没有设置转存目录，则尝试使用 115 全局转存目录
    if not target_dir_id or target_dir_id == "0":
        target_dir_id = get_config().cloud115.target_dir_id
    
    share_url = link_data.get("url")
    receive_code = link_data.get("password", "")
    
    if not target_dir_id or target_dir_id == "0":
        logger.warning("No target_dir_id configured (neither in monitor nor 115 settings). Skipping.")
        return

    if not client_115.client:
        logger.warning("115 client not initialized. Skipping auto-transfer.")
        return

    try:
        async with transfer_semaphore:
            logger.info(f"Processing new 115 link: {share_url} with pwd: {receive_code}")
            
            # 1. 转存（115 接口忽略 cid，文件始终落入最近接收/收件箱）
            filter_rules = None if link_data.get("ignore_filters") else monitor_cfg.filter_rules
            transfer_res = await client_115.share_receive(
                share_url, 
                receive_code, 
                "0",  # 115 忽略此参数
                filter_rules=filter_rules
            )
            
            # 转存后等待3秒
            await asyncio.sleep(3)
            
        if not transfer_res.get("state"):
            logger.error(f"Failed to auto-transfer link {share_url}: {transfer_res.get('error')}")
            await notify_manager.notify(
                title="[STRM] 自动转存失败",
                content=f"链接: {share_url}\n报错: {transfer_res.get('error')}"
            )
            await _update_tg_status(link_data.get('db_id'), 'failed')
            return

        logger.info(f"Successfully transferred {share_url}")

        # 2. 如果启用了 transfer 管道，通过事件总线处理
        transfer_cfg = get_config().transfer
        if transfer_cfg.enabled and transfer_cfg.temp_dir_id:
            inbox_files = await _list_inbox_files(transfer_cfg.inbox_dir_id)
            batch_task_id = link_data.get("batch_task_id") or link_data.get("task_id")
            
            await event_bus.emit(
                EVENT_TRANSFER_RECEIVED,
                share_url=share_url,
                inbox_dir_id=transfer_cfg.inbox_dir_id,
                temp_dir_id=transfer_cfg.temp_dir_id,
                files=inbox_files,
                batch_task_id=batch_task_id
            )
            logger.info(f"[Monitor] 已提交 {len(inbox_files)} 个文件到转存管道 (batch={batch_task_id})")
        
        # 3. 兼容旧逻辑：如果 transfer 未启用但有 archive_dir，走旧路径
        elif monitor_cfg.auto_organize and archive_dir_id and archive_dir_id != "0":
            logger.info("Starting auto-organize for newly transferred files (legacy)...")
            await _auto_organize(client_115, target_dir_id, archive_dir_id)

        # 4. 自动生成STRM
        if monitor_cfg.auto_strm or (transfer_cfg.enabled and transfer_cfg.auto_strm):
            logger.info("Starting auto-strm generation...")
            asyncio.create_task(sync_engine.run_sync_task())

        # 5. 推送成功通知
        await notify_manager.notify(
            title="[STRM] 自动转存成功",
            content=f"链接: {share_url}\n密码: {receive_code}\n已成功转存并加入处理队列！"
        )

        await _update_tg_status(link_data.get('db_id'), 'success')
                
    except Exception as e:
        logger.error(f"Exception during transfer: {e}")
        await _update_tg_status(link_data.get('db_id'), 'failed')


async def _update_tg_status(db_id, status: str):
    """更新 Telegram 资源库状态"""
    if not db_id:
        return
    from app.database import get_db_conn
    async with get_db_conn() as db:
        await db.execute("UPDATE tg_resources SET status = ? WHERE id = ?", (status, db_id))
        await db.commit()


async def _list_inbox_files(inbox_dir_id: str) -> list:
    """列出收件箱中的文件，标准化为统一格式"""
    files_res = await client_115.list_files(inbox_dir_id, limit=50)
    if files_res.get("error"):
        return []
    
    files = []
    for item in files_res.get("items", []):
        cid = item.get("cid") or item.get("fid") or ""
        name = item.get("n", "")
        if cid and name:
            files.append({
                "cid": cid,
                "name": name,
                "parent_cid": inbox_dir_id
            })
    return files

async def _auto_organize(client_115, source_dir_id: str, archive_dir_id: str):
    """
    扫描 source_dir_id 下的所有文件，调用 organizer 获得规范路径，
    然后在 archive_dir_id 中建立相应目录，并将文件移动过去。
    """
    from app.config import get_config
    api_type = get_config().cloud115.api_type
    
    try:
        # 递归或获取文件列表？因为刚刚转存的可能是一个文件夹
        # 使用 fs_files 遍历 target_dir_id (暂简化处理为单层)
        # TODO: 生产环境如果对方分享的是文件夹嵌套，需要递归遍历。这里假设转存的是视频文件或一层文件夹
        
        # 为了防风控，调用带 api_type 判断的方法
        files_res = await client_115.list_files(source_dir_id, limit=100)
        if files_res.get("error"):
            logger.error("Failed to list files for auto-organize.")
            return
            
        items = files_res.get("items", [])
        
        for item in items:
            if item.get("is_dir"):
                # 如果是文件夹，深入一层
                sub_res = await client_115.list_files(item["cid"], limit=100)
                sub_items = sub_res.get("items", []) if not sub_res.get("error") else []
                for sub_item in sub_items:
                    if not sub_item.get("is_dir"):
                        await _process_single_file(client_115, sub_item, archive_dir_id)
            else:
                await _process_single_file(client_115, item, archive_dir_id)
                
    except Exception as e:
        logger.error(f"Error during auto_organize: {e}")

async def _process_single_file(client_115, file_item: dict, base_archive_id: str):
    from app.core.media.organizer import organizer
    file_name = file_item.get("n", "")
    file_id = file_item.get("fid", "")
    
    # 获取刮削路径
    try:
        category, region, target_folder, target_name, tmdb_data = await organizer.get_organized_path(file_name)
    except Exception as e:
        logger.error(f"Failed to organize file {file_name}: {e}")
        return

    # 在 base_archive_id 下创建 category/region/target_folder
    # 注意：fs_mkdir 一次只能创建一层，需要逐级创建
    current_pid = base_archive_id
    path_parts = [category, region] + target_folder.split("/")
    
    for part in path_parts:
        if not part: continue
        mkdir_res = await client_115.create_folder(current_pid, part)
        if "id" in mkdir_res:
            current_pid = mkdir_res["id"]
        else:
            dirs_res = await client_115.list_dirs(current_pid)
            found = False
            for d in dirs_res.get("dirs", []):
                if d.get("n") == part:
                    current_pid = d.get("cid")
                    found = True
                    break
            if not found:
                logger.error(f"Failed to create or find folder {part}")
                return

    # 将文件重命名并移动到 current_pid
    # 1. 移动
    move_ok = await client_115.move_files([file_id], current_pid)
    if not move_ok:
        logger.error(f"Failed to move file {file_name} to {current_pid}")
        return
        
    # 2. 重命名
    if target_name != file_name:
        await client_115.rename_file(file_id, target_name)
        
    logger.info(f"Organized file {file_name} -> {target_folder}/{target_name}")

def init_handlers():
    event_bus.subscribe(EVENT_MONITOR_NEW_LINK, handle_new_link)
    logger.info("Registered Telegram link monitor handler.")
