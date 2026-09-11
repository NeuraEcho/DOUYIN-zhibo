"""
最近互动观众昵称池 - 供口播稿「随机点名」占位符 {点名} 取用

设计：
- 单例，进程内共享；弹幕/礼物/进场等互动事件到达时把观众昵称入池。
- 用定长 deque 保留最近 N 个互动昵称（含重复，越活跃越容易被点到，符合直播直觉）。
- 真实互动池为空时（刚开播还没人互动）回退到「兜底名称池」随机取；两个池都空才兜底为「用户姐姐」。
- 兜底名称池（_fallback_names）：前端配置面板可手动新增/修改，运行时热更新（不持久化）。
- fill_mentions() 把稿子里每个 {点名} 占位符独立替换为一个随机格式化昵称。
"""

import random
import re
from collections import deque
from typing import Optional

from loguru import logger

from common.nickname_formatter import format_viewer_nickname

# 口播稿里的随机点名占位符
MENTION_PLACEHOLDER = "{点名}"
_MENTION_RE = re.compile(re.escape(MENTION_PLACEHOLDER))


class ViewerPool:
    """最近互动观众昵称池（单例）"""

    _instance: Optional["ViewerPool"] = None

    def __init__(self, maxlen: int = 50):
        self._names: deque = deque(maxlen=maxlen)
        # 兜底名称池：前端手动维护，真实互动池为空时取用（运行时热更新，不持久化）
        self._fallback_names: list[str] = []

    @classmethod
    def get_instance(cls) -> "ViewerPool":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def add(self, nickname: str):
        """把一个互动观众昵称入池（空昵称忽略）"""
        name = (nickname or "").strip()
        if not name:
            return
        self._names.append(name)

    def pick_raw(self) -> Optional[str]:
        """随机取一个原始昵称：优先真实互动池，其次前端兜底名称池，都空返回 None"""
        if self._names:
            return random.choice(list(self._names))
        if self._fallback_names:
            return random.choice(self._fallback_names)
        return None

    def pick_formatted(self) -> str:
        """随机取一个并格式化为「xx姐姐」；池空兜底为「用户姐姐」"""
        return format_viewer_nickname(self.pick_raw())

    def fill_mentions(self, text: str) -> str:
        """把文本中每个 {点名} 占位符替换为一个随机格式化昵称（每处独立随机）"""
        if not text or MENTION_PLACEHOLDER not in text:
            return text
        replaced = _MENTION_RE.sub(lambda _m: self.pick_formatted(), text)
        logger.info(
            f"[ViewerPool] 随机点名替换完成: 真实互动={len(self._names)}, "
            f"兜底池={len(self._fallback_names)}, 结果='{replaced[:60]}'"
        )
        return replaced

    def clear(self):
        """清空真实互动昵称池（不影响前端兜底名称池）"""
        self._names.clear()

    # ===== 兜底名称池（前端配置面板手动维护，运行时热更新）=====
    def get_fallback_names(self) -> list[str]:
        """获取当前兜底名称池（返回副本）"""
        return list(self._fallback_names)

    def set_fallback_names(self, names: list):
        """整体覆盖兜底名称池（去空、去重、保序）"""
        seen = set()
        cleaned: list[str] = []
        for raw in names or []:
            name = (raw or "").strip()
            if name and name not in seen:
                seen.add(name)
                cleaned.append(name)
        self._fallback_names = cleaned
        logger.info(f"[ViewerPool] 兜底名称池已更新: {len(cleaned)} 个 -> {cleaned[:10]}")

    def add_fallback_name(self, name: str) -> bool:
        """新增一个兜底名称；已存在或为空返回 False"""
        name = (name or "").strip()
        if not name or name in self._fallback_names:
            return False
        self._fallback_names.append(name)
        logger.info(f"[ViewerPool] 兜底名称池新增: {name} (共 {len(self._fallback_names)} 个)")
        return True

    def remove_fallback_name(self, name: str) -> bool:
        """移除一个兜底名称；不存在返回 False"""
        name = (name or "").strip()
        if name in self._fallback_names:
            self._fallback_names.remove(name)
            logger.info(f"[ViewerPool] 兜底名称池移除: {name} (剩 {len(self._fallback_names)} 个)")
            return True
        return False

    def __len__(self) -> int:
        return len(self._names)
