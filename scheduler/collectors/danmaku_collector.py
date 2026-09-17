"""
弹幕监听客户端 - 对接直播平台开放 API/WebSocket
采集直播间弹幕消息，推送到事件总线
"""

import asyncio
import json
from typing import Optional

import websockets
from loguru import logger

from config.settings import settings
from scheduler.event_bus import EventBus, EventType, LiveEvent
from scheduler.viewer_pool import ViewerPool


class DanmakuCollector:
    """
    弹幕监听客户端

    职责：
    - 通过 WebSocket 连接直播平台
    - 实时接收弹幕消息
    - 将弹幕消息封装为 LiveEvent 推送到事件总线
    - 支持断线重连（指数退避）
    - 支持心跳保活
    - 单例：前端改直播间号后触发 reconnect() 用新 room_id 重连
    """

    _instance: Optional["DanmakuCollector"] = None

    def __init__(self):
        self._ws_url = settings.live_room.platform_ws_url
        self._room_id = settings.live_room.room_id
        self._event_bus = EventBus.get_instance()
        self._running = False
        self._ws = None
        # 重连参数（指数退避）
        self._reconnect_base_delay: float = 1.0
        self._reconnect_max_delay: float = 60.0
        self._reconnect_attempt: int = 0
        # 心跳参数
        self._heartbeat_interval: float = 30.0
        self._heartbeat_task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> "DanmakuCollector":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self):
        """启动弹幕监听（含断线重连）"""
        self._running = True
        logger.info(f"[DanmakuCollector] 启动弹幕监听, room_id={self._current_room_id()}")

        while self._running:
            try:
                ws_url = self._build_ws_url()
                if not ws_url:
                    logger.warning("[DanmakuCollector] WebSocket URL 未配置，等待中...")
                    await asyncio.sleep(5)
                    continue

                logger.info(f"[DanmakuCollector] 连接 douyinLive 本地服务: {ws_url}")

                async with websockets.connect(
                    ws_url,
                    ping_interval=self._heartbeat_interval,
                    ping_timeout=10.0,
                    close_timeout=5.0,
                ) as ws:
                    self._ws = ws
                    self._reconnect_attempt = 0  # 连接成功，重置重连计数
                    logger.info("[DanmakuCollector] WebSocket 连接成功")

                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)

            except websockets.ConnectionClosed as e:
                logger.warning(f"[DanmakuCollector] WebSocket 连接关闭: code={e.code}, reason={e.reason}")
            except websockets.InvalidURI:
                logger.error(f"[DanmakuCollector] 无效的 WebSocket URI: {self._ws_url}")
                await asyncio.sleep(5)
                continue
            except Exception as e:
                logger.error(f"[DanmakuCollector] 连接异常: {type(e).__name__}: {e}")

            if not self._running:
                break

            # 指数退避重连
            delay = min(
                self._reconnect_base_delay * (2 ** self._reconnect_attempt),
                self._reconnect_max_delay,
            )
            self._reconnect_attempt += 1
            logger.info(f"[DanmakuCollector] {delay:.1f}秒后重连 (第{self._reconnect_attempt}次)")
            await asyncio.sleep(delay)

    async def stop(self):
        """停止弹幕监听"""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws:
            await self._ws.close()
        logger.info("[DanmakuCollector] 弹幕监听已停止")

    async def reconnect(self):
        """主动断开当前连接，触发用最新 room_id 重连（前端改直播间号后调用）。

        start() 的连接循环在 ws 关闭后会重新调用 _build_ws_url() 取最新 room_id，
        这里重置退避计数让重连尽快发生（无需等指数退避）。
        """
        self._reconnect_attempt = 0  # 重置退避计数，尽快重连
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"[DanmakuCollector] 关闭旧连接异常: {type(e).__name__}: {e}")
        logger.info(f"[DanmakuCollector] 已触发重连，将使用最新 room_id={self._current_room_id()}")

    def _current_room_id(self) -> str:
        """当前生效的直播间号：优先前端运行时覆盖值（config_router），否则用 .env 默认。"""
        try:
            from web.routers.config_router import get_room_id
            return (get_room_id() or self._room_id or "").strip()
        except Exception:
            return (self._room_id or "").strip()

    def _build_ws_url(self) -> str:
        """构造 douyinLive 本地服务的 WebSocket 连接地址。

        douyinLive 的房间号从 URL 路径提取：ws://host:port/ws/<roomID>。
        - 若 platform_ws_url 已包含 /ws/ 路径，视为完整地址直接使用；
        - 否则用 room_id 拼接为 {base}/ws/{room_id}，便于只改 ROOM_ID 换直播间。
        room_id 每次连接动态读取，故前端改直播间号 + reconnect() 即可换房间。
        """
        base = (self._ws_url or "").strip()
        if not base:
            return ""
        room_id = self._current_room_id()
        if "/ws/" in base or not room_id:
            return base
        return f"{base.rstrip('/')}/ws/{room_id}"

    async def _handle_message(self, raw_message: str):
        """处理 douyinLive 本地弹幕服务推送的消息。

        douyinLive 推送两类 JSON 文本：
        1. 系统状态消息：type="system"、event="live_status"，含开播(ROOM_ONLINE)/
           未开播(ROOM_OFFLINE)/下播(ROOM_ENDED)/无效(ROOM_NOT_FOUND)/
           风控(ROOM_STATUS_UNKNOWN)等状态；
        2. 直播业务消息：抖音 protobuf 转 JSON，用 method 区分类型
           （WebcastChatMessage 弹幕 / WebcastGiftMessage 礼物 /
            WebcastLikeMessage 点赞 / WebcastMemberMessage 进场 ...），
           弹幕内容在 content 字段，发言用户昵称在 user.nickname。
        此外服务端会对客户端文本 "ping" 回复文本 "pong"。
        """
        # 文本心跳（douyinLive 对 "ping" 回 "pong"），直接忽略
        if raw_message in ("ping", "pong"):
            return

        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug(f"[DanmakuCollector] 非 JSON 消息，忽略: {raw_message[:100]}")
            return

        if not isinstance(data, dict):
            return

        # 1. 系统状态消息（开播/未开播/下播/无效/风控）
        if data.get("type") == "system":
            self._handle_system_status(data)
            return

        # 2. 直播业务消息，按 method 分发
        method = data.get("method", "")
        if method == "WebcastChatMessage":
            await self._handle_chat(data)
        elif method == "WebcastGiftMessage":
            gift = data.get("gift", {})
            gift_name = gift.get("name", "") if isinstance(gift, dict) else ""
            gift_user = self._extract_nickname(data)
            # 送礼观众昵称入池，供口播稿随机点名
            ViewerPool.get_instance().add(gift_user)
            logger.info(f"[DanmakuCollector] 礼物: {gift_user} 赠送了 {gift_name}")
        elif method in (
            "WebcastMemberMessage", "WebcastSocialMessage", "WebcastFansclubMessage",
        ):
            # 进场/关注/粉丝团：互动观众昵称入池，供随机点名
            ViewerPool.get_instance().add(self._extract_nickname(data))
            logger.debug(f"[DanmakuCollector] {method}")
        elif method == "WebcastLikeMessage":
            # 点赞：高频事件，仅 debug，不入池（避免刷屏挤占昵称池）
            logger.debug(f"[DanmakuCollector] {method}")
        else:
            logger.debug(f"[DanmakuCollector] 未处理的消息类型: {method}")

    def _extract_nickname(self, data: dict) -> str:
        """从业务消息中提取发言用户昵称（user.nickname）。"""
        user = data.get("user", {})
        if isinstance(user, dict):
            return user.get("nickname", "") or user.get("nickName", "") or user.get("name", "")
        return ""

    async def _handle_chat(self, data: dict):
        """处理弹幕消息 WebcastChatMessage → 发布 DANMAKU_MESSAGE 事件到事件总线。"""
        content = data.get("content", "")
        if not content:
            return

        user_name = self._extract_nickname(data)
        display_text = f"[{user_name}]: {content}" if user_name else content

        # 互动观众昵称入池，供口播稿「随机点名」{点名} 取用
        ViewerPool.get_instance().add(user_name)

        logger.debug(f"[DanmakuCollector] 弹幕: {display_text}")

        event = LiveEvent(
            event_type=EventType.DANMAKU_MESSAGE,
            source="danmaku",
            content=display_text,
            metadata={
                "user_name": user_name,
                "raw_content": content,   # 下游 _event_loop 取此字段作为提问内容
                "raw_data": data,
            },
        )
        await self._event_bus.publish(event)

    def _handle_system_status(self, data: dict):
        """处理 douyinLive 系统状态消息（开播/未开播/下播/无效/风控）。"""
        code = data.get("code", "")
        status_text = data.get("status_text", "") or data.get("message", "")
        if code == "ROOM_ONLINE":
            logger.info(f"[DanmakuCollector] 直播间已开播，开始接收弹幕: {status_text}")
        elif code == "ROOM_NOT_FOUND":
            logger.error(f"[DanmakuCollector] 直播间不存在或房间号无效: {status_text}")
        elif code == "ROOM_STATUS_UNKNOWN":
            logger.warning(f"[DanmakuCollector] 直播状态暂时无法确认（验证码/风控/Cookie 失效）: {status_text}")
        else:
            # ROOM_OFFLINE / ACCOUNT_OFFLINE_NO_ROOM / ROOM_ENDED 等：保持连接等待开播
            logger.info(f"[DanmakuCollector] 直播间状态: {code or 'unknown'} - {status_text}（保持连接等待）")
