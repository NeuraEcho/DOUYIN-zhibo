"""
事件总线 - 统一分发三类事件：自动播报 | 观众弹幕 | 人工干预
借鉴 Pipecat 的 Frame 双向流思想
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from loguru import logger


class EventType(str, Enum):
    """事件类型"""
    AUTO_BROADCAST = "auto_broadcast"    # 定时自动播报
    DANMAKU_MESSAGE = "danmaku_message"  # 观众弹幕
    MANUAL_COMMAND = "manual_command"    # 人工干预指令
    INTERRUPT = "interrupt"              # 打断信号


@dataclass
class LiveEvent:
    """直播事件对象"""
    event_type: EventType
    source: str          # 事件来源标识
    content: str         # 事件内容
    metadata: dict = None  # 附加元数据

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class EventBus:
    """
    全局事件总线（单例）

    职责：
    - 统一接收来自各事件采集器的事件
    - 分发事件给全局会话管理器
    - 支持事件监听器注册（用于日志、监控）
    """

    _instance: Optional["EventBus"] = None

    def __init__(self):
        self._listeners: dict[EventType, list[Callable]] = {}
        self._event_queue: asyncio.Queue[LiveEvent] = asyncio.Queue()

    @classmethod
    def get_instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def subscribe(self, event_type: EventType, handler: Callable):
        """注册事件监听器"""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(handler)
        logger.debug(f"[EventBus] 注册监听器: {event_type.value}")

    async def publish(self, event: LiveEvent):
        """发布事件"""
        logger.info(f"[EventBus] 发布事件: type={event.event_type.value}, source={event.source}")
        await self._event_queue.put(event)

        # 通知所有监听器
        handlers = self._listeners.get(event.event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(f"[EventBus] 监听器执行异常: {e}")

    async def consume(self) -> LiveEvent:
        """消费事件（阻塞等待）"""
        return await self._event_queue.get()

    def clear(self):
        """清空事件队列"""
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
