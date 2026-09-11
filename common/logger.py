"""
统一日志模块 - 基于 loguru，提供全局日志配置
跨层公共组件，所有模块统一使用此日志器
"""

import sys
from pathlib import Path

from loguru import logger

from config.settings import settings


def setup_logger():
    """初始化全局日志配置"""
    # 移除默认 handler
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        level=settings.log.level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
               "<level>{message}</level>",
    )

    # 文件输出
    log_path = Path(settings.log.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_path),
        level=settings.log.level,
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    return logger


# 初始化并导出全局 logger
setup_logger()
