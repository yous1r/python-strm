"""安全边界校验：确保文件操作不超出允许范围"""
from typing import Set
from loguru import logger

_allowed_dirs: Set[str] = set()
_source_dirs: Set[str] = set()
_target_dirs: Set[str] = set()


def init_scope(temp_dir_id: str, archive_dir_id: str, inbox_dir_id: str = "0"):
    """初始化安全范围"""
    global _allowed_dirs, _source_dirs, _target_dirs
    _source_dirs = {inbox_dir_id, temp_dir_id}
    _target_dirs = {temp_dir_id, archive_dir_id}
    _allowed_dirs = {inbox_dir_id, temp_dir_id, archive_dir_id}


def expand_allowed_dirs(dir_cid: str):
    """添加子目录到允许范围"""
    _allowed_dirs.add(dir_cid)


def validate(source_cid: str, target_cid: str) -> bool:
    """校验操作是否在安全范围内"""
    if source_cid in _source_dirs or source_cid in _allowed_dirs:
        if target_cid in _target_dirs or target_cid in _allowed_dirs:
            return True
    logger.warning(
        f"[Scope] 拒绝越界操作: source={source_cid} -> target={target_cid}"
    )
    return False


def is_in_allowed(cid: str) -> bool:
    """检查 cid 是否在允许范围内"""
    return cid in _allowed_dirs