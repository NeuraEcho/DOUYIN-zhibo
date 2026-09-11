"""
弹幕意图过滤器 - 关键词匹配 + 冷却限流
决定哪些弹幕应该触发打断，哪些只是普通评论忽略掉。
"""

import time
from typing import Optional

from loguru import logger

from config.settings import settings


class DanmakuFilter:
    """
    弹幕意图过滤器（单例）

    职责：
    - 关键词匹配：弹幕包含任一打断关键词即触发
    - 冷却限流：连续 N 秒内只响应一次打断，防止刷屏频繁打断
    - 长度过滤：忽略字数过短的弹幕（纯表情、单字等）
    """

    _instance: Optional["DanmakuFilter"] = None

    def __init__(self):
        self._keywords: list[str] = settings.danmaku.keyword_list
        self._cooldown: float = settings.danmaku.cooldown_seconds
        self._min_length: int = settings.danmaku.min_text_length
        # 上次触发打断的时间戳
        self._last_interrupt_time: float = 0.0

        logger.info(
            f"[DanmakuFilter] 初始化: keywords={self._keywords}, "
            f"cooldown={self._cooldown}s, min_length={self._min_length}"
        )

    @classmethod
    def get_instance(cls) -> "DanmakuFilter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def should_interrupt(self, text: str) -> bool:
        """
        判断弹幕是否应该触发打断

        :param text: 弹幕文本内容
        :return: True=触发打断, False=忽略
        """
        # 1. 长度过滤：太短的弹幕忽略
        if len(text) < self._min_length:
            logger.debug(f"[DanmakuFilter] 弹幕太短（{len(text)}字），忽略: {text}")
            return False

        # 2. 冷却检查：距上次打断不足冷却时间则忽略
        now = time.time()
        elapsed = now - self._last_interrupt_time
        if elapsed < self._cooldown:
            logger.debug(
                f"[DanmakuFilter] 冷却中（{elapsed:.1f}s < {self._cooldown}s），忽略: {text}"
            )
            return False

        # 3. 关键词匹配：任一命中即触发
        text_lower = text.lower()
        for keyword in self._keywords:
            if keyword.lower() in text_lower:
                self._last_interrupt_time = now
                logger.info(
                    f"[DanmakuFilter] 弹幕命中关键词 '{keyword}'，触发打断: {text}"
                )
                return True

        logger.debug(f"[DanmakuFilter] 弹幕未命中任何关键词，忽略: {text}")
        return False

    def reset_cooldown(self):
        """手动重置冷却计时器（用于测试或特殊场景）"""
        self._last_interrupt_time = 0.0

    def get_config(self) -> dict:
        """获取当前过滤规则（供前端配置面板展示）"""
        return {
            "keywords": list(self._keywords),
            "cooldown_seconds": self._cooldown,
            "min_text_length": self._min_length,
        }

    def update_config(self, keywords=None, cooldown_seconds=None, min_text_length=None):
        """
        运行时热更新过滤规则（前端配置面板调用）。
        仅更新传入的非 None 项；不持久化到 .env，重启恢复默认。
        """
        if keywords is not None:
            self._keywords = [kw.strip() for kw in keywords if kw and kw.strip()]
        if cooldown_seconds is not None:
            self._cooldown = max(0.0, float(cooldown_seconds))
        if min_text_length is not None:
            self._min_length = max(0, int(min_text_length))
        logger.info(
            f"[DanmakuFilter] 过滤规则已热更新: keywords={self._keywords}, "
            f"cooldown={self._cooldown}s, min_length={self._min_length}"
        )
