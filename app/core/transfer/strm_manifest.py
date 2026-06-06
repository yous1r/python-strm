from __future__ import annotations

from datetime import UTC, datetime


def build_manifest_record(
    *,
    cloud_type: str,
    file_id: str,
    archive_dir_id: str,
    archive_rel_path: str,
    strm_rel_path: str,
    strm_abs_path: str,
    play_identity: str,
    status: str = "generated",
    task_id: str | None = None,
) -> dict:
    """Build a normalized STRM manifest record for later persistence."""

    return {
        "cloud_type": cloud_type,
        "file_id": file_id,
        "archive_dir_id": archive_dir_id,
        "archive_rel_path": archive_rel_path,
        "strm_rel_path": strm_rel_path,
        "strm_abs_path": strm_abs_path,
        "play_identity": play_identity,
        "status": status,
        "task_id": task_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }


__all__ = ["build_manifest_record"]