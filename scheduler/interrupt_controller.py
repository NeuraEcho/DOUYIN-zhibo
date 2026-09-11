"""
中断抢占控制器 - 弹幕实时打断的核心
收到打断信号时执行原子动作序列
"""

from typing import Optional

from loguru import logger

from scheduler.event_bus import EventBus, EventType, LiveEvent
from scheduler.session_manager import SessionManager


class InterruptController:
    """
    中断抢占控制器（单例）

    职责：
    收到打断信号时执行一套原子动作：
    ① 终止当前正在运行的 LangGraph 任务
    ② 通知网关取消正在请求的 DeepSeek-R1 流与 Speech-2.8-Turbo 流
    ③ 清空全局音频播放队列
    ④ 将会话重置为空闲状态，准备处理新指令

    这是「双中断机制」中的全局 abort 抢占中断（任务级强制终止），
    区别于 LangGraph interrupt（节点边界优雅暂停）。
    """

    _instance: Optional["InterruptController"] = None

    def __init__(self):
        self._session_manager = SessionManager.get_instance()
        self._event_bus = EventBus.get_instance()

    @classmethod
    def get_instance(cls) -> "InterruptController":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def handle_interrupt(self, event: LiveEvent):
        """
        处理打断事件

        :param event: 触发打断的事件（弹幕/人工指令）
        """
        if not self._session_manager.is_busy:
            logger.info("[InterruptController] 当前无运行中会话，无需打断")
            return

        logger.warning(
            f"[InterruptController] 收到打断信号: "
            f"type={event.event_type.value}, source={event.source}"
        )

        # 执行抢占式中断（原子动作序列）
        await self._session_manager.abort()

        # 中断完成后，将新事件推入事件总线准备新一轮会话
        await self._event_bus.publish(event)

        logger.info("[InterruptController] 打断处理完成，新事件已推入")
