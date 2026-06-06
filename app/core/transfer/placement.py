"""统一归档与 STRM 的落位计算。"""

from dataclasses import dataclass
import os

from app.core.media.parser import parse_filename

from .classifier import build_archive_path
from .models import ClassifyResult


@dataclass(frozen=True)
class ArchivePlacement:
    archive_rel_path: str
    strm_rel_dir: str
    target_file_name: str
    strm_file_name: str


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


__all__ = [
    "ArchivePlacement",
    "build_archive_placement",
]
