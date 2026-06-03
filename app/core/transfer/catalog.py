"""目录树构建器：在 115 网盘中创建/查找分类目录路径 — 带防呆校验"""
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
    """
    if not base_cid or base_cid == "0":
        raise CatalogIntegrityError(
            f"[Catalog] 基目录 cid 不能为根目录(0)，path_parts={path_parts}。"
            f"请检查 archive_dir_id 配置是否正确。"
        )

    current_cid = base_cid
    for part in path_parts:
        if not part:
            continue

        logger.info(f"[Catalog] 在 cid={current_cid} 下创建/查找目录: '{part}'")

        # 创建文件夹
        mkdir_res = await client_115.create_folder(current_cid, part)
        if "id" in mkdir_res:
            new_cid = mkdir_res["id"]
            # 校验：新创建的文件夹父cid必须等于 current_cid
            if not await _verify_parent(new_cid, current_cid, part):
                raise CatalogIntegrityError(
                    f"[Catalog] 目录创建异常！在 cid={current_cid} 下创建 '{part}'，"
                    f"返回 cid={new_cid}，但验证父cid失败（可能不在预期位置）。中止！"
                )
            current_cid = new_cid
            expand_allowed_dirs(current_cid)
            logger.info(f"[Catalog] ✓ 已创建目录: '{part}' → cid={new_cid}")
            continue

        # 创建失败，查找同名目录
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


async def _verify_parent(child_cid: str, expected_parent_cid: str, dir_name: str) -> bool:
    """
    验证目录的实际父cid是否与预期一致。
    通过 list_dirs 在预期父目录下查找该 cid。
    """
    try:
        dirs_res = await client_115.list_dirs(expected_parent_cid)
        for d in dirs_res.get("dirs", []):
            if d.get("cid") == child_cid and d.get("n") == dir_name:
                return True
        logger.error(
            f"[Catalog] 父cid校验失败: 在 expected_parent={expected_parent_cid} "
            f"下未找到 child_cid={child_cid}({dir_name})"
        )
        return False
    except Exception as e:
        logger.error(f"[Catalog] 父cid校验异常: {e}")
        return False