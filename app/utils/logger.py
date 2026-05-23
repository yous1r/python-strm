import sys
import os
from loguru import logger
from app.config import get_config

def setup_logger():
    """配置日志系统"""
    config = get_config()
    
    # 移除默认的处理器
    logger.remove()
    
    # 添加控制台处理器
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=config.log.level
    )
    
    # 确保日志目录存在
    log_file = config.log.file
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # 添加文件处理器
    logger.add(
        log_file,
        rotation=config.log.rotation,
        retention=config.log.retention,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=config.log.level,
        encoding="utf-8"
    )
    
    return logger

def get_logger():
    """获取配置好的logger"""
    return logger
