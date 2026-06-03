"""目录树构建器：在 115 网盘中创建/查找分类目录路径"""
from typing import Optional
from loguru import logger
from app.core.cloud115.client import client_115
from app.core.transfer.scope import expand_allowed_dirs


async def ensure_path(base_cid: str, path_parts: list) -> Optional[str]:
    """
    在 base_cid 下逐级创建/查找目录路径。
    返回最终目录的 cid，任何一步失败返回 None。
    """
    current_cid = base_cid
    for part in path_parts:
        if not part:
            continue

        # 尝试创建文件夹，返回 {"id": ..., "name": ...} 或 {"error": ...}
        mkdir_res = await client_115.create_folder(current_cid, part)
        if "id" in mkdir_res:
            current_cid = mkdir_res["id"]
            expand_allowed_dirs(current_cid)
            logger.debug(f"[Catalog] 已创建/定位目录: {part} (cid={current_cid})")
            continue

        # 创建失败，查找已存在的同名目录
        dirs_res = await client_115.list_dirs(current_cid)
        found = False
        for d in dirs_res.get("dirs", []):
            if d.get("n") == part:
                current_cid = d.get("cid")
                expand_allowed_dirs(current_cid)
                found = True
                logger.debug(f"[Catalog] 找到已有目录: {part} (cid={current_cid})")
                break

        if not found:
            logger.error(f"[Catalog] 创建/查找目录失败: {part} under cid={current_cid}")
            return None

    return current_cid