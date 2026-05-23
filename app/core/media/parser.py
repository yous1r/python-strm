from guessit import guessit
from pydantic import BaseModel
from typing import Optional, List
from app.utils.helpers import is_video_file

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
    if not is_video_file(filename):
        return MediaInfo(title=filename, original_filename=filename)
        
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
            
    info.resolution = guessed.get("screen_size")
    info.source = guessed.get("source")
    info.codec = guessed.get("video_codec")
    info.audio = guessed.get("audio_codec")
    info.quality = guessed.get("release_group")
    
    return info
