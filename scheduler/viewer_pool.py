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

# 口播稿里的随机点名占位符（标准写法：半角 {点名}）
MENTION_PLACEHOLDER = "{点名}"
# 兼容中文输入法常见变体：全角大括号 ｛点名｝、大括号内夹空格、半/全角混用，
# 避免因占位符写法不完全一致而静默不替换（这是「点名没写进稿子」的最常见原因）。
_MENTION_RE = re.compile(r"[{｛]\s*点名\s*[}｝]")

# 「随机点名」取样窗口：只从最近互动的队尾 N 个昵称里随机选（越新越可能被点到）。
# 池本身不去重——重复发言的观众会刷新到队尾，从而稳定落在这个「最新窗口」内。
RECENT_PICK_WINDOW = 10


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
        """把一个互动观众昵称入池（空昵称忽略）。

        不去重：每次互动都追加到队尾，deque(maxlen) 自动挤掉最老的。
        因此「之前发过、现在又发」的观众会刷新为最新，稳定进入点名取样窗口。
        """
        name = (nickname or "").strip()
        if not name:
            return
        self._names.append(name)

    def pick_raw(self) -> Optional[str]:
        """随机取一个原始昵称：只从「最近互动」的队尾窗口（最新 RECENT_PICK_WINDOW 个）里选，
        越新越可能被点到；真实池空则回退前端兜底名称池，都空返回 None。"""
        if self._names:
            recent = list(self._names)[-RECENT_PICK_WINDOW:]   # 只取最新的几个
            return random.choice(recent)
        if self._fallback_names:
            return random.choice(self._fallback_names)
        return None

    def pick_formatted(self) -> str:
        """随机取一个并格式化为「xx姐姐」；池空兜底为「用户姐姐」"""
        return format_viewer_nickname(self.pick_raw())

    def fill_mentions(self, text: str) -> str:
        """把文本中每个 {点名}/｛点名｝ 占位符替换为一个随机格式化昵称（每处独立随机）"""
        if not text:
            return text
        if not _MENTION_RE.search(text):
            # 稿子里出现「点名」字样却不是可识别的占位符格式（如用了【点名】、%点名%，或大括号内
            # 夹了其他字符），明确告警并原样返回，方便定位「点名没写进稿子」的问题。
            if "点名" in text:
                logger.warning(
                    "[ViewerPool] 稿子含「点名」但未匹配到占位符格式，无法替换；"
                    "请使用半角 {点名} 或全角 ｛点名｝（大括号内除空格外不要夹其他字符）。"
                    f"原文片段='{text[:80]}'"
                )
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

    def get_recent_names(self) -> list[str]:
        """真实互动池昵称（按最近互动倒序，最新在前；不去重——重复发言的观众会出现多次，
        真实反映「谁最近在互动」），供前端实时监控展示。"""
        return list(reversed(self._names))

    def get_pool_status(self) -> dict:
        """昵称池实时状态：真实互动池 + 兜底池（供前端监控看板轮询展示）"""
        return {
            "real_names": self.get_recent_names(),   # 最近互动倒序的真实昵称（最新在前，不去重）
            "real_total": len(self._names),          # 含重复的互动条数
            "real_unique": len(set(self._names)),    # 独立观众数
            "maxlen": self._names.maxlen or 0,       # 池上限（默认 50，滚动淘汰）
            "fallback_names": list(self._fallback_names),
        }

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
