import re
import asyncio
from typing import Optional, Callable, Any
from loguru import logger

def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    if not name:
        return ""
    # 替换 Windows 禁用字符
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def format_size(size: int) -> str:
    """格式化文件大小"""
    if not size:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

def is_video_file(filename: str) -> bool:
    """判断是否为视频文件"""
    exts = ('.mp4', '.mkv', '.avi', '.ts', '.flv', '.rmvb', '.mov', '.wmv', '.webm', '.m2ts')
    return filename.lower().endswith(exts)

def is_subtitle_file(filename: str) -> bool:
    """判断是否为字幕文件"""
    exts = ('.ass', '.srt', '.ssa', '.sub', '.vtt')
    return filename.lower().endswith(exts)

def extract_year(name: str) -> Optional[int]:
    """从名称中提取年份"""
    match = re.search(r'(19|20)\d{2}', name)
    if match:
        return int(match.group(0))
    return None

async def async_retry(func: Callable, max_retries: int = 3, delay: float = 1.0, *args, **kwargs) -> Any:
    """异步重试装饰器/包装器"""
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Function {func.__name__} failed after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Attempt {attempt + 1} failed for {func.__name__}: {e}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay *= 2  # 指数退避
