from guessit import guessit
from pydantic import BaseModel
from typing import Optional, List
from app.utils.helpers import is_video_file
import re

class MediaInfo(BaseModel):
    title: str
    year: Optional[int] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    quality: Optional[str] = None
    codec: Optional[str] = None
    audio: Optional[str] = None
    resolution: Optional[str] = None
    source: Optional[str] = None
    media_type: str = "movie"  # movie, episode
    original_filename: str = ""

def parse_filename(filename: str) -> MediaInfo:
    """使用guessit解析媒体文件名"""
    guessed = guessit(filename)
    
    # 构建 MediaInfo 对象
    info = MediaInfo(
        title=guessed.get("title", filename),
        original_filename=filename
    )
    
    if "year" in guessed:
        info.year = guessed["year"]
        
    if "season" in guessed:
        info.season = guessed["season"]
        info.media_type = "episode"
        
    if "episode" in guessed:
        info.episode = guessed["episode"]
        info.media_type = "episode"
        
    if "type" in guessed:
        if guessed["type"] == "episode":
            info.media_type = "episode"

    # 某些批量标题没有视频扩展名，但仍包含 S01E01 这类清晰季集标记。
    if info.media_type == "movie":
        season_episode_match = re.search(r"S(\d{1,2})E(\d{1,3})", filename, flags=re.IGNORECASE)
        if season_episode_match:
            info.season = int(season_episode_match.group(1))
            info.episode = int(season_episode_match.group(2))
            info.media_type = "episode"

    if not is_video_file(filename) and not guessed.get("title"):
        info.title = filename
            
    info.resolution = guessed.get("screen_size")
    info.source = guessed.get("source")
    info.codec = guessed.get("video_codec")
    info.audio = guessed.get("audio_codec")
    info.quality = guessed.get("release_group")
    
    return info
