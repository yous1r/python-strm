from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime

class CloudAccount(BaseModel):
    id: Optional[int] = None
    type: str
    name: str
    cookie: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class MediaItem(BaseModel):
    id: str
    cloud_type: str
    name: str
    parent_id: str
    size: Optional[int] = 0
    is_dir: bool = False
    pickcode: Optional[str] = None
    created_at: Optional[datetime] = None

class StrmRecord(BaseModel):
    id: Optional[int] = None
    file_id: str
    cloud_type: str
    strm_path: str
    created_at: Optional[datetime] = None

class OrganizeHistory(BaseModel):
    id: Optional[int] = None
    task_id: str
    cloud_type: str
    source_id: str
    status: str
    details: Optional[str] = None
    created_at: Optional[datetime] = None

class EmbyInstance(BaseModel):
    id: str
    name: str
    url: str
    api_key: str
    created_at: Optional[datetime] = None

class SyncHistory(BaseModel):
    id: Optional[int] = None
    task_name: str
    status: str
    duration: Optional[float] = 0.0
    processed_count: Optional[int] = 0
    error_details: Optional[str] = None
    created_at: Optional[datetime] = None
