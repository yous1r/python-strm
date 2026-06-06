import os
import time
import asyncio
import urllib.parse
from pathlib import Path

import aiofiles
from loguru import logger

from app.config import get_config
from app.core.cloud115.client import client_115
from app.core.transfer.classifier import classify
from app.core.transfer.placement import build_archive_placement
from app.core.transfer.strm_manifest import build_manifest_record, list_records_for_rewrite
from app.utils.helpers import is_video_file
from app.core.media.organizer import organizer

class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.timestamps = []

    async def acquire(self):
        now = time.time()
        self.timestamps = [t for t in self.timestamps if now - t <= self.period]
        if len(self.timestamps) >= self.max_calls:
            sleep_time = self.period - (now - self.timestamps[0])
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                now = time.time()
                self.timestamps = [t for t in self.timestamps if now - t <= self.period]
        self.timestamps.append(now)

class StrmGenerator115:
    def __init__(self):
        self.client = client_115
        # 允许瞬间并发10个请求（按批处理），但限制在2秒内最多10个，防止触发 WAF
        self.rate_limiter = RateLimiter(max_calls=5, period=5.0)

    def build_strm_content(self, pickcode: str, file_name: str, base_url: str) -> str:
        config = get_config()
        encoded_name = urllib.parse.quote(file_name)
        strm_content = f"{base_url.rstrip('/')}/api/v1/115/play/{pickcode}/{encoded_name}"
        if config.cloud115.play_ua:
            strm_content += f"|User-Agent={config.cloud115.play_ua}"
        return strm_content

    async def generate_strm(
        self,
        pickcode: str,
        file_name: str,
        current_dir: str,
        root_dir: str,
        base_url: str,
        skip_organize: bool = False,
    ) -> str:
        """生成单个STRM文件，支持智能刮削打平"""
        config = get_config()
        strm_content = self.build_strm_content(pickcode, file_name, base_url)
        
        if config.organize.enabled and not skip_organize:
            # 智能整理模式：忽略网盘原生路径，打平为 大类/地区/特定名称
            category, region, target_folder, target_name, tmdb_data = await organizer.get_organized_path(file_name)
            target_dir = os.path.join(root_dir, category, region, target_folder)
            
            base_name = os.path.splitext(target_name)[0]
            strm_filename = f"{base_name}.strm"
            strm_path = os.path.join(target_dir, strm_filename)
            
            # 同时生成 NFO
            media_type = "movie" if category == "电影" else "episode"
            await organizer.write_nfo_file(target_dir, target_name, tmdb_data, media_type)
        else:
            # 原生模式：保留网盘的目录嵌套结构
            base_name = os.path.splitext(file_name)[0]
            strm_filename = f"{base_name}.strm"
            strm_path = os.path.join(current_dir, strm_filename)
        
        # 确保目录存在
        os.makedirs(os.path.dirname(strm_path), exist_ok=True)
        
        try:
            async with aiofiles.open(strm_path, mode='w', encoding='utf-8') as f:
                await f.write(strm_content)
            return strm_path
        except Exception as e:
            logger.error(f"Failed to write STRM file {strm_path}: {e}")
            return ""

    async def batch_generate(self, dir_id: str, output_dir: str, base_url: str, recursive: bool = True, root_output_dir: str = None, force: bool = False) -> list[str]:
        """批量生成STRM文件"""
        if root_output_dir is None:
            root_output_dir = output_dir

        generated = []
        limit = 1000
        offset = 0

        while True:
            # 防风控：使用按批限流器，允许瞬间迸发，降低请求频率惩罚
            await self.rate_limiter.acquire()
            
            res = await self.client.list_files_local_first(dir_id=dir_id, limit=limit, offset=offset)
            if "error" in res:
                logger.error(f"Batch generate error: {res['error']}")
                break

            items = res.get("items", [])
            if not items:
                break
                
            # If force is False, query the database for existing items in this batch
            existing_fids = set()
            if not force:
                file_ids = [str(item.get("fid")) for item in items if "fid" in item]
                if file_ids:
                    from app.database import get_db_conn
                    try:
                        async with get_db_conn() as db:
                            placeholders = ",".join(["?"] * len(file_ids))
                            query = f"SELECT file_id FROM strm_records WHERE cloud_type='115' AND file_id IN ({placeholders})"
                            async with db.execute(query, file_ids) as cursor:
                                rows = await cursor.fetchall()
                                existing_fids = {str(row["file_id"]) for row in rows}
                    except Exception as e:
                        logger.error(f"Failed to fetch existing fids: {e}")
            # 统计本目录的文件情况：如果所有文件都已处理，跳过子目录递归
            total_files = sum(1 for item in items if "fid" in item and is_video_file(item.get("n", "")))
            skipped_files = 0
            
            for item in items:
                # 文件夹处理
                if "fid" not in item:
                    if recursive and (skipped_files < total_files or force):
                        # 流控：递归子目录前延迟
                        await asyncio.sleep(1.5)
                        folder_name = item.get("n", "")
                        folder_id = str(item.get("cid"))
                        sub_dir = os.path.join(output_dir, folder_name)
                        sub_generated = await self.batch_generate(folder_id, sub_dir, base_url, recursive, root_output_dir, force)
                        generated.extend(sub_generated)
                    elif recursive:
                        logger.debug(f"Skipping fully processed subdirectory: {item.get('n', '')}")
                else:
                    # 文件处理
                    file_id = str(item.get("fid", ""))
                    if file_id in existing_fids and not force:
                        skipped_files += 1
                        logger.debug(f"Skipping already generated file: {item.get('n')}")
                        continue
                        
                    file_name = item.get("n", "")
                    skipped_files += 0  # not skipped
                    if is_video_file(file_name):
                        pickcode = item.get("pc", "")
                        if pickcode:
                            strm_path = await self.generate_strm(pickcode, file_name, output_dir, root_output_dir, base_url)
                            if strm_path:
                                generated.append(strm_path)
                                # Record to DB
                                try:
                                    from app.database import get_db_conn
                                    async with get_db_conn() as db:
                                        await db.execute('''
                                            INSERT OR IGNORE INTO strm_records (file_id, cloud_type, strm_path)
                                            VALUES (?, ?, ?)
                                        ''', (file_id, '115', strm_path))
                                        await db.commit()
                                except Exception as e:
                                    logger.error(f"Failed to record STRM in DB: {e}")
                                
            # 分页逻辑
            if len(items) < limit:
                break
            offset += limit
            
        return generated

    async def _record_manifest(
        self,
        *,
        file_id: str,
        archive_dir_id: str,
        archive_rel_path: str,
        strm_rel_path: str,
        strm_abs_path: str,
        pickcode: str,
        task_id: str | None = None,
    ) -> None:
        from app.database import get_db_conn

        record = build_manifest_record(
            cloud_type="115",
            file_id=file_id,
            archive_dir_id=archive_dir_id,
            archive_rel_path=archive_rel_path,
            strm_rel_path=strm_rel_path,
            strm_abs_path=strm_abs_path,
            play_identity=pickcode,
            task_id=task_id,
        )

        try:
            async with get_db_conn() as db:
                await db.execute(
                    '''
                    INSERT INTO strm_records (
                        cloud_type, file_id, archive_dir_id, archive_rel_path,
                        strm_rel_path, strm_abs_path, play_identity, task_id, strm_path, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cloud_type, file_id) DO UPDATE SET
                        archive_dir_id=excluded.archive_dir_id,
                        archive_rel_path=excluded.archive_rel_path,
                        strm_rel_path=excluded.strm_rel_path,
                        strm_abs_path=excluded.strm_abs_path,
                        play_identity=excluded.play_identity,
                        task_id=excluded.task_id,
                        strm_path=excluded.strm_path,
                        status=excluded.status,
                        updated_at=excluded.updated_at
                    ''',
                    (
                        record["cloud_type"],
                        record["file_id"],
                        record["archive_dir_id"],
                        record["archive_rel_path"],
                        record["strm_rel_path"],
                        record["strm_abs_path"],
                        record["play_identity"],
                        record["task_id"],
                        record["strm_abs_path"],
                        record["status"],
                        record["updated_at"],
                    ),
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to record STRM manifest: {e}")

    async def generate_strm_for_folder(self, folder_cid: str, share_files: list, strm_subdir: str = "", root_output_dir: str = "", task_id: str | None = None) -> list:
        """按文件夹批量生成STRM：list_files一次拿到全部pickcode，按SHA1匹配生成"""
        config = get_config()
        base_url = config.strm.base_url
        output_dir = root_output_dir or config.strm.output_dir
        if strm_subdir:
            output_dir = os.path.join(output_dir, strm_subdir)

        # 建立 SHA1 -> 文件名的映射
        sha_to_name = {}
        for sf in share_files:
            s = sf.get("sha", "").upper()
            if s:
                sha_to_name[s] = sf.get("name", "")

        generated = []
        # list_files 一次拿到全部 pickcode
        list_res = await self.client.list_files(folder_cid, limit=1000)
        if list_res.get("error"):
            logger.error(f"[STRM batch] list_files failed: {list_res['error']}")
            return generated

        for item in list_res.get("items", []):
            fid = item.get("fid", "")
            fname = item.get("n", "")
            pc = item.get("pc", "")
            sha_val = item.get("sha", item.get("sha1", "")).upper() if hasattr(item, 'get') else ""

            if not pc or not fname:
                continue

            # 按SHA1匹配，确定文件名
            matched_name = sha_to_name.get(sha_val, fname)
            if not is_video_file(matched_name):
                continue

            strm_rel_path = ""
            if strm_subdir:
                classify_result = await classify(matched_name)
                if classify_result:
                    placement = build_archive_placement(classify_result, matched_name)
                    strm_rel_path = f"{placement.strm_rel_dir}/{placement.strm_file_name}"

            strm_path = await self.generate_strm(
                pc,
                matched_name,
                root_output_dir or output_dir,
                root_output_dir or output_dir,
                base_url,
                skip_organize=bool(strm_subdir),
            )
            if strm_path:
                generated.append(strm_path)
                if strm_subdir and strm_rel_path:
                    await self._record_manifest(
                        file_id=str(fid),
                        archive_dir_id=folder_cid,
                        archive_rel_path=strm_subdir,
                        strm_rel_path=strm_rel_path,
                        strm_abs_path=strm_path,
                        pickcode=pc,
                        task_id=task_id,
                    )
                else:
                    await self._record_manifest(
                        file_id=str(fid),
                        archive_dir_id=folder_cid,
                        archive_rel_path=strm_subdir,
                        strm_rel_path=str(Path(strm_path).name),
                        strm_abs_path=strm_path,
                        pickcode=pc,
                        task_id=task_id,
                    )

        logger.info(f"[STRM batch] Generated {len(generated)} STRMs for folder {folder_cid}")
        return generated

    async def rewrite_manifest_records(self, records: list[dict], base_url: str = "") -> list[str]:
        """根据已记录的 manifest 重写 STRM 文件内容。"""
        config = get_config()
        target_base_url = base_url or config.strm.base_url
        rewritten: list[str] = []

        for record in records:
            strm_path = record.get("strm_abs_path") or record.get("strm_path") or ""
            pickcode = record.get("play_identity") or ""
            file_name = Path(record.get("strm_rel_path") or strm_path).stem
            file_name = f"{file_name}.mkv" if file_name else record.get("file_id") or "video.mkv"
            if not strm_path or not pickcode:
                continue

            strm_content = self.build_strm_content(pickcode, file_name, target_base_url)
            os.makedirs(os.path.dirname(strm_path), exist_ok=True)
            try:
                async with aiofiles.open(strm_path, mode='w', encoding='utf-8') as f:
                    await f.write(strm_content)
                rewritten.append(strm_path)
            except Exception as e:
                logger.error(f"Failed to rewrite STRM file {strm_path}: {e}")

        return rewritten

    async def rewrite_from_manifest(self, archive_root: str = "", base_url: str = "") -> dict[str, object]:
        """Rewrite generated STRM files from persisted manifest records."""

        records = await list_records_for_rewrite(archive_root)
        rewritten = await self.rewrite_manifest_records(records, base_url=base_url)
        return {
            "archive_root": archive_root,
            "rewritten": len(rewritten),
            "files": rewritten[:10],
        }

generator_115 = StrmGenerator115()
