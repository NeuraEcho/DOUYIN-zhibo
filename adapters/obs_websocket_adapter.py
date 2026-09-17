"""
obs-websocket v5 异步适配器 - 让 Web 后台一键控制 OBS（推流起停 / 填推流地址+推流码 / 切场景）

协议说明（OBS 28+ 内置 obs-websocket 5.x，无需额外装插件，但要在
「工具 → obs-websocket 设置」里勾选「启用 obs-websocket」）：

1. 客户端连上 ws://host:port 后，服务端先发 Hello(op=0)，里面可能带 authentication(salt/challenge)
2. 客户端回 Identify(op=1)：rpcVersion=1 + 鉴权串（有鉴权时）+ eventSubscriptions=0（本模块不订阅事件）
3. 服务端回 Identified(op=2) → 握手完成
4. 之后每次请求：客户端发 Request(op=6){requestType, requestId, requestData}
   → 服务端回 RequestResponse(op=7){requestId, requestStatus{result, code, comment}, responseData}
5. op=8 (Reconnect) 表示服务端要求重连，这里直接当错误抛出（本模块是「短连接按需请求」模型）

鉴权算法（官方规定，两次 base64(sha256(...)) 摘要）：
    secret = base64(sha256(password + salt))
    auth   = base64(sha256(secret + challenge))

连接模型：每个请求独立「握手 → 发请求 → 收响应 → 关闭」，用 asyncio.Lock 串行化，
避免前端轮询状态与用户点击操作互相抢同一条 socket；本机连接握手仅几毫秒，开销可忽略。
"""

import asyncio
import base64
import hashlib
import json
import uuid
from typing import Any, Optional

import websockets
from loguru import logger

from common.exceptions import ObsAdapterException
from config.settings import settings


# ============ obs-websocket v5 操作码 ============
OP_HELLO = 0            # 服务端 → 客户端：连接建立，携带鉴权挑战
OP_IDENTIFY = 1         # 客户端 → 服务端：表明身份 + 鉴权
OP_IDENTIFIED = 2       # 服务端 → 客户端：握手成功
OP_RECONNECT = 8        # 服务端 → 客户端：要求重连（会话迁移）

# 不订阅任何事件（本模块只做「发请求拿响应」，事件流会白白占带宽）
EVENT_SUBSCRIPTIONS_NONE = 0

# obs-websocket 的 RPC 版本，5.x 固定为 1
RPC_VERSION = 1


class ObsWebsocketAdapter:
    """obs-websocket v5 客户端（单例）"""

    _instance: Optional["ObsWebsocketAdapter"] = None

    def __init__(self):
        cfg = settings.obs
        self._enabled: bool = cfg.enabled
        self._host: str = cfg.host
        self._port: int = cfg.port
        self._password: str = cfg.password
        self._timeout: float = cfg.timeout
        self._stream_service_type: str = cfg.stream_service_type
        # 串行锁：同一时刻只有一条 OBS 连接在握手/收发，避免并发互踩
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "ObsWebsocketAdapter":
        """获取全局单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # =================================================================
    # 连接配置（支持后台运行时改，不必重启；改完立即对下一次请求生效）
    # =================================================================

    def get_config(self) -> dict[str, Any]:
        """当前连接配置（密码只报「是否已设置」，不回传明文）"""
        return {
            "enabled": self._enabled,
            "host": self._host,
            "port": self._port,
            "password_set": bool(self._password),
            "timeout": self._timeout,
            "stream_service_type": self._stream_service_type,
            "ws_url": self._ws_url(),
        }

    def update_config(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        password: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        """运行时更新连接配置（仅内存生效，重启回到 .env 的值）"""
        if host is not None and host.strip():
            self._host = host.strip()
        if port is not None and 0 < port < 65536:
            self._port = port
        if password is not None:
            self._password = password
        if enabled is not None:
            self._enabled = bool(enabled)
        logger.info(f"[obs] 连接配置已更新: {self._ws_url()}, 鉴权={'开' if self._password else '关'}")
        return self.get_config()

    def _ws_url(self) -> str:
        return f"ws://{self._host}:{self._port}"

    def _ensure_enabled(self) -> None:
        if not self._enabled:
            raise ObsAdapterException("OBS 控制未启用，请在后台开启或把 .env 的 OBS_ENABLED 设为 true")

    # =================================================================
    # 协议底层
    # =================================================================

    @staticmethod
    def _compute_auth(password: str, salt: str, challenge: str) -> str:
        """按 obs-websocket v5 规定计算鉴权串：base64(sha256(base64(sha256(pwd+salt)) + challenge))"""
        secret = base64.b64encode(
            hashlib.sha256((password + salt).encode("utf-8")).digest()
        ).decode("utf-8")
        return base64.b64encode(
            hashlib.sha256((secret + challenge).encode("utf-8")).digest()
        ).decode("utf-8")

    async def _recv_json(self, ws, what: str) -> dict[str, Any]:
        """收一条 JSON 文本帧，超时/非法帧统一转成友好异常"""
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
        except asyncio.TimeoutError:
            raise ObsAdapterException(f"等待 OBS 返回 {what} 超时（{self._timeout}s）")
        except websockets.ConnectionClosed as e:
            # 4008 = 鉴权失败，是最常见的配错场景（密码不对），单独给明确提示
            if e.code == 4008:
                raise ObsAdapterException("OBS 鉴权失败：obs-websocket 服务器密码不正确")
            raise ObsAdapterException(f"OBS 提前断开了连接（等待 {what} 时）：{e.reason or e.code}")

        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            raise ObsAdapterException(f"OBS 返回的 {what} 不是合法 JSON")
        if not isinstance(msg, dict) or "op" not in msg:
            raise ObsAdapterException(f"OBS 返回的 {what} 缺少 op 字段")
        return msg

    async def _handshake(self, ws) -> str:
        """完成 Hello → Identify → Identified 握手，返回协商到的 OBS 版本信息"""
        hello = await self._recv_json(ws, "Hello")
        if hello.get("op") != OP_HELLO:
            raise ObsAdapterException(f"OBS 首帧不是 Hello(op=0)，实际 op={hello.get('op')}")

        hello_data = hello.get("d") or {}
        identify_payload: dict[str, Any] = {
            "rpcVersion": RPC_VERSION,
            "eventSubscriptions": EVENT_SUBSCRIPTIONS_NONE,
        }

        auth = hello_data.get("authentication")
        if auth:
            if not self._password:
                raise ObsAdapterException(
                    "OBS 开启了鉴权，但后台没填密码；请在「OBS 推流控制」卡片填入 "
                    "obs-websocket 服务器密码（或到 OBS 里关掉鉴权）"
                )
            identify_payload["authentication"] = self._compute_auth(
                self._password, auth.get("salt", ""), auth.get("challenge", "")
            )

        await ws.send(json.dumps({"op": OP_IDENTIFY, "d": identify_payload}))

        identified = await self._recv_json(ws, "Identified")
        op = identified.get("op")
        if op == OP_RECONNECT:
            raise ObsAdapterException("OBS 要求重连（Reconnect），本次请求中止，请重试")
        if op != OP_IDENTIFIED:
            # 鉴权失败时服务端会直接关闭连接并给出 close code，这里统一提示
            comment = (identified.get("d") or {}).get("comment", "")
            raise ObsAdapterException(f"OBS 握手失败（op={op}）：{comment or '请检查密码是否正确'}")

        return str(hello_data.get("obsWebSocketVersion", "unknown"))

    async def request(self, request_type: str, request_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """向 OBS 发一个请求并返回 responseData（失败抛 ObsAdapterException）

        Args:
            request_type: obs-websocket 请求名，如 GetStreamStatus / StartStream
            request_data: 请求参数，无参数时传 None
        """
        self._ensure_enabled()
        async with self._lock:
            return await self._request_locked(request_type, request_data)

    async def _request_locked(self, request_type: str, request_data: Optional[dict[str, Any]]) -> dict[str, Any]:
        url = self._ws_url()
        request_id = uuid.uuid4().hex
        try:
            async with websockets.connect(url, open_timeout=self._timeout, close_timeout=2) as ws:
                ws_version = await self._handshake(ws)

                await ws.send(json.dumps({
                    "op": 6,  # Request
                    "d": {
                        "requestType": request_type,
                        "requestId": request_id,
                        **({"requestData": request_data} if request_data else {}),
                    },
                }))

                # 正常只有一条 RequestResponse；容错跳过可能的其他帧
                for _ in range(5):
                    msg = await self._recv_json(ws, f"{request_type} 响应")
                    if msg.get("op") != 7:
                        continue
                    d = msg.get("d") or {}
                    if d.get("requestId") != request_id:
                        continue
                    status = d.get("requestStatus") or {}
                    if not status.get("result"):
                        raise ObsAdapterException(
                            f"OBS 执行 {request_type} 失败（code={status.get('code')}）："
                            f"{status.get('comment') or '未给出原因'}"
                        )
                    logger.debug(f"[obs] {request_type} 成功 (obs-websocket {ws_version})")
                    return d.get("responseData") or {}

                raise ObsAdapterException(f"OBS 没有返回 {request_type} 的响应")

        except ObsAdapterException:
            raise
        except OSError as e:
            # 连接被拒绝 / 主机不可达：OBS 没开，或 obs-websocket 没启用，或端口不对
            raise ObsAdapterException(
                f"连不上 OBS（{url}）：{e}。请确认 OBS 已启动，且「工具 → obs-websocket 设置」里"
                f"已勾选「启用 obs-websocket」，端口为 {self._port}"
            )
        except websockets.InvalidStatus as e:
            raise ObsAdapterException(f"OBS 拒绝了 WebSocket 握手（{url}）：{e}")
        except asyncio.TimeoutError:
            raise ObsAdapterException(f"连接 OBS 超时（{url}，{self._timeout}s）")

    # =================================================================
    # 业务能力封装（只暴露后台真正要用的动作）
    # =================================================================

    async def get_version(self) -> dict[str, Any]:
        """OBS 版本信息（也用来做连通性测试）"""
        return await self.request("GetVersion")

    async def get_stream_status(self) -> dict[str, Any]:
        """推流状态：outputActive / outputTimecode / outputState ..."""
        return await self.request("GetStreamStatus")

    async def start_stream(self) -> None:
        """开始推流（OBS 侧已在推流时会报错，调用方先查状态更友好）"""
        await self.request("StartStream")

    async def stop_stream(self) -> None:
        """停止推流"""
        await self.request("StopStream")

    async def get_stream_service_settings(self) -> dict[str, Any]:
        """读取当前推流服务设置：{type, settings:{server,key,...}, bandwidth}"""
        return await self.request("GetStreamServiceSettings")

    async def set_stream_service_settings(self, server: str, key: str) -> dict[str, Any]:
        """写入推流地址 + 推流码（直播伴侣给的那两串）

        做法：先读出当前设置，只替换 server/key，其余字段（含 bandwidth）原样回写，
        避免把 OBS 里已有的其他推流参数清掉。
        """
        current = await self.get_stream_service_settings()
        current_settings = dict(current.get("settings") or {})
        current_settings["server"] = server
        current_settings["key"] = key

        request_data: dict[str, Any] = {
            "type": current.get("type") or self._stream_service_type,
            "settings": current_settings,
        }
        # bandwidth 有的 OBS 版本必须回传，有就带上原值
        if current.get("bandwidth") is not None:
            request_data["bandwidth"] = current["bandwidth"]

        logger.info(f"[obs] 写入推流地址: server={server}, key={self.mask_key(key)}")
        return await self.request("SetStreamServiceSettings", request_data)

    async def get_scene_list(self) -> list[dict[str, Any]]:
        """场景列表（按 sceneIndex 升序返回）"""
        data = await self.request("GetSceneList")
        scenes = data.get("scenes") or []
        return sorted(scenes, key=lambda s: s.get("sceneIndex", 0))

    async def get_current_scene(self) -> str:
        """当前正在播出（Program）的场景名"""
        data = await self.request("GetCurrentProgramScene")
        return str(data.get("sceneName") or "")

    async def set_current_scene(self, scene_name: str) -> None:
        """切换当前播出场景"""
        await self.request("SetCurrentProgramScene", {"sceneName": scene_name})

    async def get_full_status(self) -> dict[str, Any]:
        """聚合状态：一次给前端渲染整张卡片所需的全部信息"""
        version = await self.get_version()
        stream = await self.get_stream_status()
        service = await self.get_stream_service_settings()
        scenes_data = await self.request("GetSceneList")

        scenes = sorted(scenes_data.get("scenes") or [], key=lambda s: s.get("sceneIndex", 0))
        svc_settings = service.get("settings") or {}

        return {
            "connected": True,
            "obs_version": version.get("obsVersion", ""),
            "obs_websocket_version": version.get("obsWebSocketVersion", ""),
            "streaming": bool(stream.get("outputActive")),
            "reconnecting": bool(stream.get("outputReconnecting")),
            "timecode": stream.get("outputTimecode", "00:00:00"),
            "output_state": stream.get("outputState", ""),
            "congestion": stream.get("outputCongestion", 0),
            "skipped_frames": stream.get("outputSkippedFrames", 0),
            "total_frames": stream.get("outputTotalFrames", 0),
            "stream_type": service.get("type", ""),
            "server": svc_settings.get("server", ""),
            "key_masked": self.mask_key(str(svc_settings.get("key", ""))),
            "scenes": [s.get("sceneName", "") for s in scenes],
            "current_scene": scenes_data.get("currentProgramSceneName", ""),
        }

    # =================================================================
    # 一键开播编排
    # =================================================================

    async def one_click_start(
        self,
        server: str = "",
        key: str = "",
        scene_name: str = "",
        start_stream: bool = True,
    ) -> dict[str, Any]:
        """一键开播：填推流码（给了才填）→ 切场景（给了才切）→ 开始推流

        每一步都做「已完成则跳过」的幂等判断，重复点不会报错。
        注意：推流进行中 OBS 不允许改推流服务设置，这种情况会明确提示先停播。
        """
        steps: list[str] = []

        stream_status = await self.get_stream_status()
        already_streaming = bool(stream_status.get("outputActive"))

        # 1) 写推流地址 + 推流码
        if server.strip() or key.strip():
            if already_streaming:
                raise ObsAdapterException("OBS 正在推流中，无法修改推流地址/推流码，请先停止推流再改")
            current = await self.get_stream_service_settings()
            cur_settings = current.get("settings") or {}
            new_server = server.strip() or str(cur_settings.get("server", ""))
            new_key = key.strip() or str(cur_settings.get("key", ""))
            if new_server == cur_settings.get("server") and new_key == cur_settings.get("key"):
                steps.append("推流地址/推流码已是目标值，跳过")
            else:
                await self.set_stream_service_settings(new_server, new_key)
                steps.append(f"已写入推流地址 {new_server}，推流码 {self.mask_key(new_key)}")

        # 2) 切场景
        if scene_name.strip():
            current_scene = await self.get_current_scene()
            if current_scene == scene_name:
                steps.append(f"场景已在「{scene_name}」，跳过")
            else:
                await self.set_current_scene(scene_name)
                steps.append(f"已切到场景「{scene_name}」")

        # 3) 开始推流
        if start_stream:
            if already_streaming:
                steps.append("OBS 已在推流中，跳过")
            else:
                await self.start_stream()
                steps.append("已开始推流")

        logger.info(f"[obs] 一键开播完成: {' | '.join(steps)}")
        return {"steps": steps}

    # =================================================================
    # 工具
    # =================================================================

    @staticmethod
    def mask_key(key: str) -> str:
        """推流码脱敏显示：只留头尾，避免后台截图泄露"""
        if not key:
            return ""
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}****{key[-4:]}"
