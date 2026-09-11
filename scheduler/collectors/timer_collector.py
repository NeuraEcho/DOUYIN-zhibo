"""
定时任务触发器 - 自动轮播脚本播报
基于 APScheduler 实现定时任务调度
"""

import asyncio
from typing import Optional

from loguru import logger

from scheduler.event_bus import EventBus, EventType, LiveEvent


class TimerCollector:
    """
    定时任务触发器

    职责：
    - 按配置的间隔时间触发自动播报事件
    - 从脚本库中选取播报内容
    - 将事件推送到事件总线
    """

    def __init__(self):
        self._event_bus = EventBus.get_instance()
        self._running = False
        self._interval_seconds: int = 180  # 默认 3 分钟

    async def start(self):
        """启动定时任务"""
        self._running = True
        logger.info(f"[TimerCollector] 启动定时任务, 间隔={self._interval_seconds}秒")

        while self._running:
            await asyncio.sleep(self._interval_seconds)
            if not self._running:
                break

            # TODO: 从脚本库选取播报内容
            event = LiveEvent(
                event_type=EventType.AUTO_BROADCAST,
                source="timer",
                content="[定时播报] 欢迎来到直播间！",  # 占位脚本内容
            )
            await self._event_bus.publish(event)

    async def stop(self):
        """停止定时任务"""
        self._running = False
        logger.info("[TimerCollector] 定时任务已停止")

    def set_interval(self, seconds: int):
        """设置播报间隔"""
        self._interval_seconds = seconds
        logger.info(f"[TimerCollector] 播报间隔已更新: {seconds}秒")
