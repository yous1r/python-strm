"""目录树构建器：在 115 网盘中创建/查找分类目录路径 — 带防呆校验"""
import asyncio
from typing import Optional
from loguru import logger
from app.core.cloud115.client import client_115
from app.core.transfer.scope import expand_allowed_dirs


class CatalogIntegrityError(Exception):
    """目录完整性校验失败，中止所有后续操作"""
    pass


async def ensure_path(base_cid: str, path_parts: list) -> Optional[str]:
    """
    在 base_cid 下逐级创建/查找目录路径。
    每步操作后校验实际父cid与预期一致，不一致则抛出 CatalogIntegrityError 中止。
    返回最终目录的 cid，任何一步失败返回 None。
    每级目录间延迟 2s 防止 115 405 风控。
    """
    if not base_cid or base_cid == "0":
        raise CatalogIntegrityError(
            f"[Catalog] 基目录 cid 不能为根目录(0)，path_parts={path_parts}。"
            f"请检查 archive_dir_id 配置是否正确。"
        )

    current_cid = base_cid
    for i, part in enumerate(path_parts):
        if not part:
            continue

        # 流控：每级目录间延迟 2s
        if i > 0:
            await asyncio.sleep(2.0)

        logger.info(f"[Catalog] 在 cid={current_cid} 下创建/查找目录: '{part}'")

        # 创建文件夹（create_folder 内部已处理"已存在"的情况，返回已有目录的 cid）
        mkdir_res = await client_115.create_folder(current_cid, part)
        if "id" in mkdir_res:
            new_cid = mkdir_res["id"]
            current_cid = new_cid
            expand_allowed_dirs(current_cid)
            logger.info(f"[Catalog] ✓ 已创建/定位目录: '{part}' → cid={new_cid}")
            continue

        # 创建失败且不是"已存在"的情况，尝试查找
        dirs_res = await client_115.list_dirs(current_cid)
        found = False
        for d in dirs_res.get("dirs", []):
            if d.get("n") == part:
                current_cid = d.get("cid")
                expand_allowed_dirs(current_cid)
                found = True
                logger.info(f"[Catalog] ✓ 找到已有目录: '{part}' → cid={current_cid}")
                break

        if not found:
            raise CatalogIntegrityError(
                f"[Catalog] 目录操作失败！在 cid={current_cid} 下无法创建或找到 '{part}'。"
                f"完整路径: {'/'.join(path_parts)}。中止！"
            )

    return current_cid