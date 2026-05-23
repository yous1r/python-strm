from typing import List, Optional
import os
import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8095
    debug: bool = False

class DatabaseConfig(BaseModel):
    path: str = "data/python_strm.db"

class SyncDirConfig(BaseModel):
    dir_id: str
    name: str

class Cloud115Config(BaseModel):
    enabled: bool = False
    cookie: str = ""
    strm_type: str = "pickcode"
    sync_dirs: List[SyncDirConfig] = []

class Cloud123Config(BaseModel):
    enabled: bool = False
    access_token: str = ""
    strm_type: str = "fileid"
    sync_dirs: List[SyncDirConfig] = []

class TmdbConfig(BaseModel):
    api_key: str = ""
    language: str = "zh-CN"
    proxy: str = ""

class EmbyConfig(BaseModel):
    instances: List[dict] = []

class StrmConfig(BaseModel):
    output_dir: str = "strm_output"
    base_url: str = "http://localhost:8095"
    sync_metadata: bool = True
    clean_invalid: bool = True

class WashConfig(BaseModel):
    prefer_dolby: bool = True
    prefer_larger: bool = True

class OrganizeConfig(BaseModel):
    enabled: bool = True
    categories: List[str] = ["电影", "剧集", "动漫", "纪录片", "综艺"]
    regions: List[str] = ["国产", "欧美", "日韩", "其他"]
    wash: WashConfig = WashConfig()

class TelegramConfig(BaseModel):
    enabled: bool = False
    api_id: str = ""
    api_hash: str = ""
    channels: List[str] = []

class MonitorConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()
    poll_interval: int = 60

class WecomConfig(BaseModel):
    enabled: bool = False
    corp_id: str = ""
    corp_secret: str = ""
    agent_id: str = ""

class TelegramNotifyConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""

class BarkNotifyConfig(BaseModel):
    enabled: bool = False
    server: str = "https://api.day.app"
    device_key: str = ""
    encryption_key: str = ""
    encryption_iv: str = ""
    encryption_algorithm: str = "AES-128-CBC"

class NotifyConfig(BaseModel):
    wecom: WecomConfig = WecomConfig()
    telegram: TelegramNotifyConfig = TelegramNotifyConfig()
    bark: BarkNotifyConfig = BarkNotifyConfig()

class ProxyConfig(BaseModel):
    http: str = ""
    https: str = ""

class LogConfig(BaseModel):
    level: str = "INFO"
    file: str = "data/logs/app.log"
    rotation: str = "10 MB"
    retention: str = "7 days"

class AppConfig(BaseSettings):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    cloud115: Cloud115Config = Cloud115Config()
    cloud123: Cloud123Config = Cloud123Config()
    tmdb: TmdbConfig = TmdbConfig()
    emby: EmbyConfig = EmbyConfig()
    strm: StrmConfig = StrmConfig()
    organize: OrganizeConfig = OrganizeConfig()
    monitor: MonitorConfig = MonitorConfig()
    notify: NotifyConfig = NotifyConfig()
    proxy: ProxyConfig = ProxyConfig()
    log: LogConfig = LogConfig()

_config_instance = None

def load_config(config_path: str = "config.yaml") -> AppConfig:
    """加载配置文件并合并默认值"""
    global _config_instance
    
    config_dict = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config_dict = yaml.safe_load(f) or {}
            
    _config_instance = AppConfig(**config_dict)
    return _config_instance

def get_config() -> AppConfig:
    """获取全局配置实例"""
    global _config_instance
    if _config_instance is None:
        return load_config()
    return _config_instance

def deep_update(d, u):
    """深度合并字典"""
    import collections.abc
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = deep_update(d.get(k, {}), v)
        else:
            d[k] = v
    return d

def update_config(partial_dict: dict, config_path: str = "config.yaml") -> AppConfig:
    """使用增量数据更新配置并持久化，触发热加载"""
    global _config_instance
    current_dict = _config_instance.model_dump() if _config_instance else {}
    
    # 深度合并
    merged_dict = deep_update(current_dict, partial_dict)
    
    # Pydantic 类型安全校验 (如果传入非法参数，此处会抛出异常被上层捕获)
    new_config = AppConfig(**merged_dict)
    
    # 持久化到文件
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(new_config.model_dump(), f, allow_unicode=True, sort_keys=False)
        
    # 热替换内存单例
    _config_instance = new_config
    return _config_instance
