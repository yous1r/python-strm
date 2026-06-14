from typing import List, Optional
import os
import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings

class CategoryConfig(BaseModel):
    name: str = ""
    subcategories: List[str] = []

class SyncDirConfig(BaseModel):
    dir_id: str
    name: str

class TransferConfig(BaseModel):
    enabled: bool = False
    # temp_dir_id: str = ""
    # archive_dir_id: str = ""
    # archive_dir_name: str = "strm"
    auto_organize: bool = True
    auto_strm: bool = True
    # archive_dir: SyncDirConfig = None
    categories: List[CategoryConfig] = []

    @staticmethod
    def default_categories() -> List[CategoryConfig]:
        return [
            CategoryConfig(name="电影", subcategories=["国产电影", "欧美电影", "日韩电影", "其他"]),
            CategoryConfig(name="剧集", subcategories=["国产剧集", "欧美剧集", "日韩剧集", "其他"]),
            CategoryConfig(name="动漫", subcategories=["国产动漫", "日韩动漫", "欧美动漫", "其他"]),
            CategoryConfig(name="纪录片", subcategories=["国产纪录片", "日韩纪录片", "欧美纪录片", "其他"]),
            CategoryConfig(name="综艺", subcategories=["国产综艺", "日韩综艺", "欧美综艺", "其他"]),
        ]

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8095
    debug: bool = False

class DatabaseConfig(BaseModel):
    path: str = "data/strm.db"

class Cloud115Config(BaseModel):
    enabled: bool = False
    cookie: str = ""
    strm_type: str = "pickcode"
    play_ua: str = ""
    api_type: str = "app"  # 'app' or 'web'
    target_dir_id: str = "0"
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

class EmbyStrmPathMappingConfig(BaseModel):
    emby_prefix: str = ""
    local_prefix: str = ""


class EmbyProxyInstanceConfig(BaseModel):
    media_server_type: str = "emby"
    name: str = ""
    url: str = ""
    api_key: str = ""
    proxy_port: int = 0
    strm_path_mappings: List[EmbyStrmPathMappingConfig] = []

class EmbyProxyConfig(BaseModel):
    enabled: bool = False
    preheat_on_full_sync: bool = False
    instances: List[EmbyProxyInstanceConfig] = []

class EmbyConfig(BaseModel):
    proxy: EmbyProxyConfig = EmbyProxyConfig()

class StrmConfig(BaseModel):
    output_dir: str = "strm"
    base_url: str = "http://localhost:8095"
    sync_metadata: bool = True
    clean_invalid: bool = True

class WashConfig(BaseModel):
    prefer_dolby: bool = True
    prefer_larger: bool = True

class Cloud115OrganizeConfig(BaseModel):
    enabled: bool = False
    source_dirs: List[str] = []
    target_dir: str = ""

class OrganizeConfig(BaseModel):
    enabled: bool = True
    categories: List[str] = ["电影", "剧集", "动漫", "纪录片", "综艺"]
    regions: List[str] = ["国产", "欧美", "日韩", "其他"]
    wash: WashConfig = WashConfig()
    cloud115: Cloud115OrganizeConfig = Cloud115OrganizeConfig()


class TelegramHistorySyncConfig(BaseModel):
    mode: str = "relative_range"
    relative_value: int = 6
    relative_unit: str = "months"
    date_start: str = ""
    date_end: str = ""
    chunk_days: int = 7
    emit_new_link_events: bool = False
    scheduled_enabled: bool = True
    scheduled_interval_minutes: int = 5


class TelegramConfig(BaseModel):
    enabled: bool = False
    api_id: str = ""
    api_hash: str = ""
    bot_token: str = ""
    channels: List[str] = []
    proxy: str = ""
    keywords: List[str] = []
    filter_rules: List[str] = []
    target_dir_id: str = "0"
    archive_dir_id: str = "0"
    auto_organize: bool = False
    auto_strm: bool = False
    mode: str = "auto"
    history_sync: TelegramHistorySyncConfig = TelegramHistorySyncConfig()
    startup_sync: str = "latest"
    history_limit: int = 100
    reconnect_backoff: int = 5
    scheduled_sync_enabled: bool = True
    scheduled_sync_interval_minutes: int = 5
    scheduled_sync_limit: int = 20
    full_sync_skip_if_scheduled_within_minutes: int = 20
    transfer_concurrency: int = 2
    transfer_cooldown_seconds: float = 0

class StartupPipelineConfig(BaseModel):
    """启动时工作流执行全局配置"""
    enabled: bool = False # 全局开关
    run_db_sync: bool = True  # 是否开启全量同步缓存库 
    run_telegram_sync: bool = True  # 是否开启Telegram频道监控
    run_strm_sync: bool = True  # 是否开启STRM生成

class MonitorConfig(BaseModel):
    telegram: TelegramConfig = TelegramConfig()
    startup_pipeline: StartupPipelineConfig = StartupPipelineConfig()
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
    proxy: str = ""

class BarkNotifyConfig(BaseModel):
    enabled: bool = False
    server: str = "https://api.day.app"
    device_key: str = ""
    device_keys: List[str] = []
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
    transfer: TransferConfig = TransferConfig()
    notify: NotifyConfig = NotifyConfig()
    proxy: ProxyConfig = ProxyConfig()
    log: LogConfig = LogConfig()

_config_instance = None

def load_config(config_path: str = "data/config.yaml") -> AppConfig:
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

def update_config(partial_dict: dict, config_path: str = "data/config.yaml") -> AppConfig:
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
