from __future__ import annotations

from datetime import UTC, datetime

from app.database import get_db_conn


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


async def list_records_for_rewrite(archive_root: str = "") -> list[dict]:
    """Load generated STRM manifest records, optionally scoped to an archive root."""

    query = """
        SELECT cloud_type, file_id, archive_dir_id, archive_rel_path,
               strm_rel_path, strm_abs_path, play_identity, task_id, strm_path, status
        FROM strm_records
        WHERE status='generated'
    """
    params: list[str] = []

    if archive_root:
        normalized_root = archive_root.strip().strip("/")
        query += " AND archive_rel_path LIKE ?"
        params.append(f"{normalized_root}%")

    query += " ORDER BY id ASC"

    async with get_db_conn() as db:
        cursor = await db.execute(query, tuple(params))
        rows = await cursor.fetchall()

    return [dict(row) for row in rows]


__all__ = ["build_manifest_record", "list_records_for_rewrite"]