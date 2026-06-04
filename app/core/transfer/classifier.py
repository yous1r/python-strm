"""分类规则引擎：确定文件应归档到的分类路径"""
import re
from typing import Optional
from loguru import logger
from app.core.media.organizer import organizer as media_organizer
from app.config import get_config
from .models import ClassifyResult


def _sanitize(name: str) -> str:
    """移除文件名中的非法字符，取纯作品名"""
    # 去掉扩展名、去掉常见分隔符后面的季集信息
    import os
    base = os.path.splitext(name)[0]
    # 去掉 " - S01E03" 这类季集标记
    base = re.sub(r'\s*[-–]\s*S\d+E\d+.*$', '', base, flags=re.IGNORECASE)
    # 去掉末尾的季集信息
    base = re.sub(r'\s*[-–]\s*第?[0-9]+[集话].*$', '', base)
    return base.strip()


async def classify(file_name: str) -> Optional[ClassifyResult]:
    """
    根据文件名进行 TMDB 刮削，返回分类结果。
    复用现有 MediaOrganizer 的刮削能力。
    """
    try:
        config = get_config()
        categories_config = config.transfer.categories
        if not categories_config:
            categories_config = config.transfer.default_categories()

        # 调用现有 organizer：返回 (category, region, target_folder, target_name, tmdb_data)
        category, region, target_folder, target_name, tmdb_data = await media_organizer.get_organized_path(file_name)

        # 提取 TMDB ID
        tmdb_id = ""
        if tmdb_data:
            tmdb_id = str(tmdb_data.get("id", ""))

        # 从 TMDB 数据中提取标题和年份（不解析 target_name，因为可能包含剧集信息）
        title = ""
        year = ""
        if tmdb_data:
            title = tmdb_data.get("title") or tmdb_data.get("name") or ""
            year = (tmdb_data.get("release_date") or tmdb_data.get("first_air_date") or "")[:4]
        if not title:
            title = _sanitize(target_name or file_name)
        if not year:
            year_match = re.search(r'\((\d{4})\)', (target_folder or "") + (target_name or ""))
            if year_match:
                year = year_match.group(1)

        # 查找匹配的分类配置
        cat_config = None
        for c in categories_config:
            if c.name == category:
                cat_config = c
                break

        subcategory = ""
        if cat_config and cat_config.subcategories:
            region_map = {
                "国产": f"国产{category}",
                "欧美": f"欧美{category}",
                "日韩": f"日韩{category}",
                "其他": "其他"
            }
            subcategory = region_map.get(region, "其他")

        # 确定季号
        season = 1
        if tmdb_data and tmdb_data.get("season_number"):
            season = tmdb_data.get("season_number", 1)

        return ClassifyResult(
            category=category,
            subcategory=subcategory,
            title=title,
            year=year,
            tmdb_id=tmdb_id,
            season=season,
            media_type="movie" if category == "电影" else "tv"
        )

    except Exception as e:
        logger.error(f"[Classifier] 分类失败: {file_name}, error={e}")
        return None


def build_archive_path(result: ClassifyResult) -> list:
    """
    根据分类结果构建归档目录路径片段。
    电影：["电影", "电影名 (年份) {tmdb-id}"]
    剧集：["剧集", "国产剧集", "剧名 (年份) {tmdb-id}", "Season N"]
    """
    parts = [result.category]

    if result.subcategory:
        parts.append(result.subcategory)

    folder_name = f"{result.title} ({result.year})"
    if result.tmdb_id:
        folder_name += f" {{tmdb-{result.tmdb_id}}}"
    parts.append(folder_name)

    if result.media_type == "tv" and result.season > 0:
        parts.append(f"Season {result.season}")

    return parts