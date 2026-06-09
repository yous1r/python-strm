"""统一归档与 STRM 的落位计算。"""

from dataclasses import dataclass
import os
import re

from app.core.media.parser import parse_filename

from .classifier import build_archive_path
from .models import ClassifyResult


@dataclass(frozen=True)
class ArchivePlacement:
    archive_rel_path: str
    strm_rel_dir: str
    target_file_name: str
    strm_file_name: str


_SEASON_SEGMENT_RE = re.compile(r"^Season\s+\d+$", re.IGNORECASE)


def build_archive_placement(result: ClassifyResult, file_name: str) -> ArchivePlacement:
    media_info = parse_filename(file_name)
    path_parts = build_archive_path(result)
    archive_rel_path = "/".join(path_parts)
    ext = os.path.splitext(media_info.original_filename)[1]
    folder_name = path_parts[-1]

    if result.media_type == "tv":
        season = result.season or media_info.season or 1
        episode = media_info.episode or 1
        base_name = f"{result.title} - S{season:02d}E{episode:02d}"
    else:
        base_name = folder_name

    return ArchivePlacement(
        archive_rel_path=archive_rel_path,
        strm_rel_dir=archive_rel_path,
        target_file_name=f"{base_name}{ext}",
        strm_file_name=f"{base_name}.strm",
    )


def derive_series_scope_path(archive_rel_path: str) -> str:
    """将季级归档路径折叠到剧级根目录，用于整剧 STRM 批量更新。"""

    normalized = str(archive_rel_path or "").replace("\\", "/").strip("/")
    if not normalized:
        return ""

    parts = [part for part in normalized.split("/") if part]
    if parts and _SEASON_SEGMENT_RE.fullmatch(parts[-1]):
        parts = parts[:-1]

    return "/".join(parts)


__all__ = [
    "ArchivePlacement",
    "build_archive_placement",
    "derive_series_scope_path",
]
