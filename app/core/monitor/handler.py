import asyncio
from loguru import logger
from app.events import event_bus, EVENT_MONITOR_NEW_LINK
from app.config import get_config
from app.core.cloud115.client import client_115
from app.core.notify.manager import notify_manager
from app.core.media.organizer import organizer
from app.core.sync.engine import sync_engine

async def handle_new_link(link_data: dict, source: str, **kwargs):
    """
    Handle new links from Telegram or other monitors.
    link_data format: {"url": "https://115.com/s/xxxx", "password": "xxxx", "type": "115"}
    """
    if link_data.get("type") != "115":
        logger.debug(f"Ignoring non-115 link: {link_data}")
        return

    config = get_config().monitor.telegram
    target_dir_id = config.target_dir_id
    archive_dir_id = config.archive_dir_id
    
    share_url = link_data.get("url")
    receive_code = link_data.get("password", "")
    
    if not target_dir_id or target_dir_id == "0":
        logger.warning("No target_dir_id configured for 115 auto-transfer. Skipping.")
        return

    if not client_115.client:
        logger.warning("115 client not initialized. Skipping auto-transfer.")
        return

    logger.info(f"Processing new 115 link: {share_url} with pwd: {receive_code}")
    
    # 1. 尝试转存
    transfer_res = await client_115.share_receive(share_url, receive_code, target_dir_id)
    if not transfer_res.get("state"):
        logger.error(f"Failed to auto-transfer link {share_url}: {transfer_res.get('error')}")
        await notify_manager.notify(
            title="[STRM] 自动转存失败",
            content=f"链接: {share_url}\n报错: {transfer_res.get('error')}"
        )
        return

    logger.info(f"Successfully transferred {share_url} to dir {target_dir_id}")
    
    # 2. 自动整理 (如果开启)
    if config.auto_organize and archive_dir_id and archive_dir_id != "0":
        logger.info("Starting auto-organize for newly transferred files...")
        await _auto_organize(client_115, target_dir_id, archive_dir_id)

    # 3. 自动生成STRM (如果开启)
    if config.auto_strm:
        logger.info("Starting auto-strm generation...")
        # 简单粗暴，直接触发全局同步
        # 如果要做到精准，需要传具体的 dir_id 给 sync_engine，但全局同步可以确保完整性
        asyncio.create_task(sync_engine.run_sync_task())

    # 4. 推送成功通知
    await notify_manager.notify(
        title="[STRM] 自动转存成功",
        content=f"链接: {share_url}\n密码: {receive_code}\n已成功转存并加入处理队列！"
    )

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
        if not files_res.get("state"):
            logger.error("Failed to list files for auto-organize.")
            return
            
        items = files_res.get("data", [])
        
        for item in items:
            if item.get("is_dir"):
                # 如果是文件夹，深入一层
                sub_res = await client_115.list_files(item["cid"], limit=100)
                sub_items = sub_res.get("data", []) if sub_res.get("state") else []
                for sub_item in sub_items:
                    if not sub_item.get("is_dir"):
                        await _process_single_file(client_115, sub_item, archive_dir_id)
            else:
                await _process_single_file(client_115, item, archive_dir_id)
                
    except Exception as e:
        logger.error(f"Error during auto_organize: {e}")

async def _process_single_file(client_115, file_item: dict, base_archive_id: str):
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
        # 尝试创建文件夹
        mkdir_res = await client_115.create_folder(current_pid, part)
        if mkdir_res.get("state") and "cid" in mkdir_res.get("data", {}):
            current_pid = mkdir_res["data"]["cid"]
        else:
            # 可能是文件夹已存在
            # 查找该目录下同名文件夹的 cid
            dirs_res = await client_115.list_dirs(current_pid)
            found = False
            for d in dirs_res.get("data", []):
                if d.get("n") == part:
                    current_pid = d.get("cid")
                    found = True
                    break
            if not found:
                logger.error(f"Failed to create or find folder {part}")
                return

    # 将文件重命名并移动到 current_pid
    # 1. 移动
    move_res = await client_115.move_files([file_id], current_pid)
    if not move_res:
        logger.error(f"Failed to move file {file_name} to {current_pid}")
        return
        
    # 2. 重命名
    if target_name != file_name:
        await client_115.rename_file(file_id, target_name)
        
    logger.info(f"Organized file {file_name} -> {target_folder}/{target_name}")

def init_handlers():
    event_bus.subscribe(EVENT_MONITOR_NEW_LINK, handle_new_link)
    logger.info("Registered Telegram link monitor handler.")
