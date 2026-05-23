from app.core.media.parser import MediaInfo
from loguru import logger

class VersionWasher:
    def __init__(self):
        pass

    def compare_versions(self, existing: MediaInfo, new: MediaInfo, strategy: str) -> str:
        """比较版本。返回 'replace' 或 'keep' """
        # 简单实现：按文件大小或特征判断
        if strategy == "prefer_dolby":
            new_dolby = "dv" in (new.codec or "").lower() or "dolby" in (new.original_filename).lower()
            ext_dolby = "dv" in (existing.codec or "").lower() or "dolby" in (existing.original_filename).lower()
            if new_dolby and not ext_dolby:
                return 'replace'
            elif ext_dolby and not new_dolby:
                return 'keep'
                
        # 默认返回 keep (实际项目需根据文件大小/分辨率对比)
        return 'keep'

washer = VersionWasher()
