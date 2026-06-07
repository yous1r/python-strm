from __future__ import annotations

from datetime import UTC, datetime

from app.core.cloud115.db_sync import get_local_pickcode
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
        SELECT id, cloud_type, file_id, archive_dir_id, archive_rel_path,
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


async def hydrate_playback_record(record: dict | None) -> dict | None:
    """Backfill missing 115 play_identity from the local 115 cache when possible."""

    if not record:
        return None

    if record.get("play_identity"):
        return record

    if record.get("cloud_type") != "115" or not record.get("file_id"):
        return record

    pickcode = get_local_pickcode(str(record["file_id"]))
    if not pickcode:
        return record

    updated_at = datetime.now(UTC).isoformat()
    async with get_db_conn() as db:
        await db.execute(
            """
            UPDATE strm_records
            SET play_identity=?, updated_at=?
            WHERE cloud_type=? AND file_id=?
            """,
            (pickcode, updated_at, record["cloud_type"], record["file_id"]),
        )
        await db.commit()

    hydrated = dict(record)
    hydrated["play_identity"] = pickcode
    hydrated["updated_at"] = updated_at
    return hydrated


async def get_strm_record_by_file_id(
    *,
    cloud_type: str,
    file_id: str,
) -> dict | None:
    """Load a STRM manifest record by stable cloud file identity."""

    async with get_db_conn() as db:
        cursor = await db.execute(
            """
            SELECT id, cloud_type, file_id, archive_dir_id, archive_rel_path,
                   strm_rel_path, strm_abs_path, play_identity, task_id, strm_path, status
            FROM strm_records
            WHERE cloud_type=? AND file_id=?
            LIMIT 1
            """,
            (cloud_type, file_id),
        )
        row = await cursor.fetchone()

    return await hydrate_playback_record(dict(row) if row else None)


async def get_strm_record_by_path(
    *,
    cloud_type: str,
    paths: list[str],
) -> dict | None:
    """Load a STRM manifest record by one of several possible path forms."""

    normalized_paths: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = (path or "").replace("\\", "/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_paths.append(normalized)

    if not normalized_paths:
        return None

    predicates = " OR ".join(
        "(strm_abs_path=? OR strm_path=? OR strm_rel_path=?)" for _ in normalized_paths
    )
    params: list[str] = [cloud_type]
    for path in normalized_paths:
        params.extend((path, path, path))

    query = f"""
        SELECT id, cloud_type, file_id, archive_dir_id, archive_rel_path,
               strm_rel_path, strm_abs_path, play_identity, task_id, strm_path, status
        FROM strm_records
        WHERE cloud_type=?
          AND ({predicates})
        ORDER BY CASE
            WHEN play_identity IS NOT NULL AND play_identity <> '' THEN 0
            ELSE 1
        END,
        id ASC
        LIMIT 1
    """

    async with get_db_conn() as db:
        cursor = await db.execute(query, tuple(params))
        row = await cursor.fetchone()

    return await hydrate_playback_record(dict(row) if row else None)


async def get_media_item_link(
    *,
    media_server_type: str,
    media_server_name: str,
    media_item_id: str,
    media_source_id: str = "",
) -> dict | None:
    """Load a cached media-server item to cloud playback mapping."""

    async with get_db_conn() as db:
        cursor = await db.execute(
            '''
            SELECT media_server_type, media_server_name, media_item_id, media_source_id,
                   cloud_type, file_id, play_identity, strm_record_id, source_path
            FROM media_item_links
            WHERE media_server_type=?
              AND media_server_name=?
              AND media_item_id=?
              AND media_source_id=?
            LIMIT 1
            ''',
            (media_server_type, media_server_name, media_item_id, media_source_id or ""),
        )
        row = await cursor.fetchone()

    return dict(row) if row else None


async def upsert_media_item_link(
    *,
    media_server_type: str,
    media_server_name: str,
    media_item_id: str,
    cloud_type: str,
    play_identity: str,
    media_source_id: str = "",
    file_id: str | None = None,
    strm_record_id: int | None = None,
    source_path: str | None = None,
) -> None:
    """Cache a media-server item to STRM playback identity mapping."""

    async with get_db_conn() as db:
        await db.execute(
            '''
            INSERT INTO media_item_links (
                media_server_type, media_server_name, media_item_id, media_source_id,
                cloud_type, file_id, play_identity, strm_record_id, source_path, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(media_server_type, media_server_name, media_item_id, media_source_id) DO UPDATE SET
                cloud_type=excluded.cloud_type,
                file_id=excluded.file_id,
                play_identity=excluded.play_identity,
                strm_record_id=excluded.strm_record_id,
                source_path=excluded.source_path,
                updated_at=excluded.updated_at
            ''',
            (
                media_server_type,
                media_server_name,
                media_item_id,
                media_source_id or "",
                cloud_type,
                file_id,
                play_identity,
                strm_record_id,
                source_path,
            ),
        )
        await db.commit()


__all__ = [
    "build_manifest_record",
    "get_media_item_link",
    "get_strm_record_by_file_id",
    "get_strm_record_by_path",
    "hydrate_playback_record",
    "list_records_for_rewrite",
    "upsert_media_item_link",
]