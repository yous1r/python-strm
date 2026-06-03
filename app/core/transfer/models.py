"""转存整理管道数据模型"""
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime


class OperationLog(BaseModel):
    """单条操作日志"""
    task_id: str
    seq: int
    file_cid: str
    file_name: str
    new_name: Optional[str] = None
    source_cid: str
    target_cid: str
    op_type: str  # "move" | "rename" | "create_dir"
    status: str = "done"  # "done" | "rolled_back"


class TransferTask(BaseModel):
    """转存整理任务"""
    task_id: str
    status: str = "pending"  # "running" | "done" | "rolled_back" | "failed"
    source_dir_id: str = ""
    archive_dir_id: str = ""
    file_count: int = 0
    success_count: int = 0
    error_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    operations: List[OperationLog] = []


class RollbackResult(BaseModel):
    """还原结果"""
    task_id: str
    status: str  # "done" | "partial" | "failed"
    total_ops: int
    rolled_back: int
    errors: List[str] = []


class FileInfo(BaseModel):
    """文件信息"""
    cid: str
    name: str
    parent_cid: str
    is_dir: bool = False
    size: int = 0
    pickcode: Optional[str] = None


class ClassifyResult(BaseModel):
    """分类结果"""
    category: str      # 一级分类：电影/剧集/动漫/纪录片/综艺
    subcategory: str   # 二级分类：国产剧集/欧美剧集 等，无则为空
    title: str         # 作品名
    year: str          # 年份
    tmdb_id: str       # TMDB ID
    season: int        # 季号，电影为 0
    media_type: str    # "movie" | "tv" | "anime"


class TransferRequest(BaseModel):
    """转存请求"""
    share_url: str
    receive_code: str = ""
    target_dir_id: str = ""  # 临时目录，如果不传则用配置默认值
    filter_rules: Optional[List[str]] = None