"""目录树构建器：使用 fs_makedirs_app 递归创建目录，单次 API 调用完成"""
from typing import Optional
from loguru import logger
from app.core.cloud import get_cloud_plugin
from app.core.transfer.scope import expand_allowed_dirs


class CatalogIntegrityError(Exception):
    """目录完整性校验失败，中止所有后续操作"""
    pass


async def ensure_path(base_cid: str, path_parts: list) -> Optional[str]:
    """
    在 base_cid 下递归创建目录路径。
    使用 fs_makedirs_app 单次 API 调用完成多级目录创建，避免逐级调用触发 405。
    返回最终目录的 cid。
    """
    if not base_cid or base_cid == "0":
        raise CatalogIntegrityError(
            f"[Catalog] 基目录 cid 不能为根目录(0)，path_parts={path_parts}。"
            f"请检查 archive_dir_id 配置是否正确。"
        )

    path_str = "/".join(p for p in path_parts if p)
    if not path_str:
        return base_cid

    logger.info(f"[Catalog] 递归创建路径: {path_str} (父cid={base_cid})")

    res = await get_cloud_plugin("115").client.create_path(base_cid, path_str)

    if "id" in res and res["id"]:
        final_cid = res["id"]
        expand_allowed_dirs(final_cid)
        logger.info(f"[Catalog] ✓ 已创建路径: {path_str} → cid={final_cid}")
        return final_cid

    raise CatalogIntegrityError(
        f"[Catalog] 创建路径失败: {path_str} under cid={base_cid}, error={res.get('error')}"
    )
