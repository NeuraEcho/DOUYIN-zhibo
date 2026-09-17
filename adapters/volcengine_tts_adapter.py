"""
火山引擎语音合成 TTS 适配器（豆包 Seed-TTS V3 双向流式 WebSocket）
基于已验证可运行的 protocols.py + tts_demo.py 参考实现重写

协议格式：自定义二进制帧（Message dataclass marshal/unmarshal）
帧结构：Header(4字节) + [Event(4字节) + SessionID + ConnectID] + PayloadSize(4字节) + Payload

协议流程：
1. WebSocket 连接（X-Api-Key + X-Api-Resource-Id 鉴权，subprotocols=["bidirection"]）
2. 发送 StartConnection → 收到 ConnectionStarted
3. 发送 StartSession → 收到 SessionStarted
4. 发送 TaskRequest → 立即发送 FinishSession → 接收音频流
5. 收到 SessionFinished → 发送 FinishConnection 关闭
"""

import asyncio
import io
import json
import struct
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, List, Optional

import websockets
from loguru import logger

from adapters.base import BaseTTSAdapter
from common.exceptions import SpeechAdapterException
from config.settings import settings


# =====================================================================
# 协议层（来自已验证可运行的 protocols.py）
# =====================================================================

class MsgType(IntEnum):
    """消息类型 (4 bits)"""
    Invalid = 0
    FullClientRequest = 0b1
    AudioOnlyClient = 0b10
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    FrontEndResultServer = 0b1100
    Error = 0b1111
    ServerACK = AudioOnlyServer

    def __str__(self) -> str:
        return self.name if self.name else f"MsgType({self.value})"


class MsgTypeFlagBits(IntEnum):
    """消息标志位 (4 bits)"""
    NoSeq = 0           # 无序列号
    PositiveSeq = 0b1   # 带正序列号
    LastNoSeq = 0b10    # 最后一条无序列号
    NegativeSeq = 0b11  # 最后一条带负序列号
    WithEvent = 0b100   # Payload 包含 event number (int32)


class VersionBits(IntEnum):
    Version1 = 1
    Version2 = 2


class HeaderSizeBits(IntEnum):
    HeaderSize4 = 1
    HeaderSize8 = 2
    HeaderSize12 = 3
    HeaderSize16 = 4


class SerializationBits(IntEnum):
    Raw = 0
    JSON = 0b1
    Thrift = 0b11
    Custom = 0b1111


class CompressionBits(IntEnum):
    None_ = 0
    Gzip = 0b1
    Custom = 0b1111


class EventType(IntEnum):
    """事件类型枚举"""
    None_ = 0

    # 连接事件 (1~49 上行)
    StartConnection = 1
    FinishConnection = 2

    # 连接事件 (50~99 下行)
    ConnectionStarted = 50
    ConnectionFailed = 51
    ConnectionFinished = 52

    # 会话事件 (100~149 上行)
    StartSession = 100
    CancelSession = 101
    FinishSession = 102

    # 会话事件 (150~199 下行)
    SessionStarted = 150
    SessionCanceled = 151
    SessionFinished = 152
    SessionFailed = 153
    UsageResponse = 154

    # 通用事件 (200~249 上行)
    TaskRequest = 200

    # TTS 事件 (350~399 下行)
    TTSSentenceStart = 350
    TTSSentenceEnd = 351
    TTSResponse = 352
    TTSEnded = 359

    def __str__(self) -> str:
        return self.name if self.name else f"EventType({self.value})"


@dataclass
class Message:
    """二进制帧消息对象

    帧格式：
    0                 1                 2                 3
    | 0 1 2 3 4 5 6 7 | 0 1 2 3 4 5 6 7 | 0 1 2 3 4 5 6 7 | 0 1 2 3 4 5 6 7 |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |    Version      |   Header Size   |     Msg Type    |      Flags      |
    |   (4 bits)      |    (4 bits)     |     (4 bits)    |     (4 bits)    |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    | Serialization   |   Compression   |           Reserved                |
    |   (4 bits)      |    (4 bits)     |           (8 bits)                |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                       Optional Header Extensions                      |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    |                           Payload                                     |
    +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
    """

    version: VersionBits = VersionBits.Version1
    header_size: HeaderSizeBits = HeaderSizeBits.HeaderSize4
    type: MsgType = MsgType.Invalid
    flag: MsgTypeFlagBits = MsgTypeFlagBits.NoSeq
    serialization: SerializationBits = SerializationBits.JSON
    compression: CompressionBits = CompressionBits.None_

    event: EventType = EventType.None_
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0

    payload: bytes = b""

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        """从二进制数据解析消息"""
        if len(data) < 3:
            raise ValueError(f"Data too short: expected at least 3 bytes, got {len(data)}")

        type_and_flag = data[1]
        msg_type = MsgType(type_and_flag >> 4)
        flag = MsgTypeFlagBits(type_and_flag & 0b00001111)

        msg = cls(type=msg_type, flag=flag)
        msg.unmarshal(data)
        return msg

    def marshal(self) -> bytes:
        """序列化为二进制数据"""
        buffer = io.BytesIO()

        # 写入帧头
        header = [
            (self.version << 4) | self.header_size,
            (self.type << 4) | self.flag,
            (self.serialization << 4) | self.compression,
        ]

        header_size = 4 * self.header_size
        if padding := header_size - len(header):
            header.extend([0] * padding)

        buffer.write(bytes(header))

        # 写入扩展字段
        writers = self._get_writers()
        for writer in writers:
            writer(buffer)

        return buffer.getvalue()

    def unmarshal(self, data: bytes) -> None:
        """从二进制数据反序列化"""
        buffer = io.BytesIO(data)

        # 读取 version 和 header_size
        version_and_header_size = buffer.read(1)[0]
        self.version = VersionBits(version_and_header_size >> 4)
        self.header_size = HeaderSizeBits(version_and_header_size & 0b00001111)

        # 跳过第二字节（type + flag 已在 from_bytes 中解析）
        buffer.read(1)

        # 读取 serialization 和 compression
        serialization_compression = buffer.read(1)[0]
        self.serialization = SerializationBits(serialization_compression >> 4)
        self.compression = CompressionBits(serialization_compression & 0b00001111)

        # 跳过 header padding
        header_size = 4 * self.header_size
        read_size = 3
        if padding_size := header_size - read_size:
            buffer.read(padding_size)

        # 读取扩展字段
        readers = self._get_readers()
        for reader in readers:
            reader(buffer)

    # ----- 序列化辅助 -----

    def _get_writers(self) -> List[Callable[[io.BytesIO], None]]:
        writers = []

        if self.flag == MsgTypeFlagBits.WithEvent:
            writers.extend([self._write_event, self._write_session_id])

        if self.type in [
            MsgType.FullClientRequest, MsgType.FullServerResponse,
            MsgType.FrontEndResultServer, MsgType.AudioOnlyClient,
            MsgType.AudioOnlyServer,
        ]:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                writers.append(self._write_sequence)
        elif self.type == MsgType.Error:
            writers.append(self._write_error_code)

        writers.append(self._write_payload)
        return writers

    def _get_readers(self) -> List[Callable[[io.BytesIO], None]]:
        readers = []

        if self.type in [
            MsgType.FullClientRequest, MsgType.FullServerResponse,
            MsgType.FrontEndResultServer, MsgType.AudioOnlyClient,
            MsgType.AudioOnlyServer,
        ]:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                readers.append(self._read_sequence)
        elif self.type == MsgType.Error:
            readers.append(self._read_error_code)

        if self.flag == MsgTypeFlagBits.WithEvent:
            readers.extend([self._read_event, self._read_session_id, self._read_connect_id])

        readers.append(self._read_payload)
        return readers

    def _write_event(self, buffer: io.BytesIO) -> None:
        buffer.write(struct.pack(">i", self.event))

    def _write_session_id(self, buffer: io.BytesIO) -> None:
        # 连接级事件不带 session_id
        if self.event in [
            EventType.StartConnection, EventType.FinishConnection,
            EventType.ConnectionStarted, EventType.ConnectionFailed,
        ]:
            return
        session_id_bytes = self.session_id.encode("utf-8")
        size = len(session_id_bytes)
        buffer.write(struct.pack(">I", size))
        if size > 0:
            buffer.write(session_id_bytes)

    def _write_sequence(self, buffer: io.BytesIO) -> None:
        buffer.write(struct.pack(">i", self.sequence))

    def _write_error_code(self, buffer: io.BytesIO) -> None:
        buffer.write(struct.pack(">I", self.error_code))

    def _write_payload(self, buffer: io.BytesIO) -> None:
        size = len(self.payload)
        buffer.write(struct.pack(">I", size))
        buffer.write(self.payload)

    def _read_event(self, buffer: io.BytesIO) -> None:
        event_bytes = buffer.read(4)
        if event_bytes:
            event_value = struct.unpack(">i", event_bytes)[0]
            try:
                self.event = EventType(event_value)
            except ValueError:
                self.event = event_value

    def _read_session_id(self, buffer: io.BytesIO) -> None:
        if self.event in [
            EventType.StartConnection, EventType.FinishConnection,
            EventType.ConnectionStarted, EventType.ConnectionFailed,
            EventType.ConnectionFinished,
        ]:
            return
        size_bytes = buffer.read(4)
        if size_bytes:
            size = struct.unpack(">I", size_bytes)[0]
            if size > 0:
                session_id_bytes = buffer.read(size)
                if len(session_id_bytes) == size:
                    self.session_id = session_id_bytes.decode("utf-8")

    def _read_connect_id(self, buffer: io.BytesIO) -> None:
        if self.event in [
            EventType.ConnectionStarted, EventType.ConnectionFailed,
            EventType.ConnectionFinished,
        ]:
            size_bytes = buffer.read(4)
            if size_bytes:
                size = struct.unpack(">I", size_bytes)[0]
                if size > 0:
                    self.connect_id = buffer.read(size).decode("utf-8")

    def _read_sequence(self, buffer: io.BytesIO) -> None:
        sequence_bytes = buffer.read(4)
        if sequence_bytes:
            self.sequence = struct.unpack(">i", sequence_bytes)[0]

    def _read_error_code(self, buffer: io.BytesIO) -> None:
        error_code_bytes = buffer.read(4)
        if error_code_bytes:
            self.error_code = struct.unpack(">I", error_code_bytes)[0]

    def _read_payload(self, buffer: io.BytesIO) -> None:
        size_bytes = buffer.read(4)
        if size_bytes:
            size = struct.unpack(">I", size_bytes)[0]
            if size > 0:
                self.payload = buffer.read(size)

    def __str__(self) -> str:
        event_str = str(self.event) if isinstance(self.event, EventType) else f"EventType({self.event})"
        ids = ""
        if self.session_id:
            ids += f", SessionId: {self.session_id}"
        if self.connect_id:
            ids += f", ConnectId: {self.connect_id}"
        if self.type in [MsgType.AudioOnlyServer, MsgType.AudioOnlyClient]:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                return f"MsgType: {self.type}, EventType:{event_str}{ids}, Sequence: {self.sequence}, PayloadSize: {len(self.payload)}"
            return f"MsgType: {self.type}, EventType:{event_str}{ids}, PayloadSize: {len(self.payload)}"
        elif self.type == MsgType.Error:
            return f"MsgType: {self.type}, EventType:{event_str}{ids}, ErrorCode: {self.error_code}, Payload: {self.payload.decode('utf-8', 'ignore')}"
        else:
            if self.flag in [MsgTypeFlagBits.PositiveSeq, MsgTypeFlagBits.NegativeSeq]:
                return f"MsgType: {self.type}, EventType:{event_str}{ids}, Sequence: {self.sequence}, Payload: {self.payload.decode('utf-8', 'ignore')}"
            return f"MsgType: {self.type}, EventType:{event_str}{ids}, Payload: {self.payload.decode('utf-8', 'ignore')}"


# ===== 协议辅助函数 =====

async def _receive_message(ws: websockets.WebSocketClientProtocol) -> Message:
    """从 WebSocket 接收并解析一条消息"""
    data = await ws.recv()
    if isinstance(data, str):
        raise ValueError(f"Unexpected text message: {data}")
    return Message.from_bytes(data)


async def _wait_for_event(
    ws: websockets.WebSocketClientProtocol,
    msg_type: MsgType,
    event_type: EventType,
) -> Message:
    """等待特定的消息类型+事件类型，收到错误则抛异常"""
    while True:
        msg = await _receive_message(ws)
        if msg.type == MsgType.Error:
            raise SpeechAdapterException(
                f"协议错误: code={msg.error_code}, payload={msg.payload.decode('utf-8', 'ignore')}"
            )
        if msg.type == msg_type and msg.event == event_type:
            return msg
        raise SpeechAdapterException(f"期望 {msg_type}/{event_type}, 收到 {msg}")


# =====================================================================
# 适配器层
# =====================================================================

class VolcengineTTSAdapter(BaseTTSAdapter):
    """
    火山引擎 V3 双向流式 WebSocket TTS 适配器

    基于已验证可运行的 protocols.py + tts_demo.py 参考实现重写
    鉴权：X-Api-Key + X-Api-Resource-Id 请求头
    协议：Message dataclass marshal/unmarshal 二进制帧
    输出：PCM 裸音频（默认 24kHz, 16-bit, mono）
    """

    _WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"

    def __init__(self):
        vc = settings.volcengine
        self._app_id = vc.app_id.strip()
        self._api_key = vc.api_key.strip()
        self._cluster = vc.cluster.strip()
        self._speaker = vc.voice_type.strip()
        self._encoding = vc.encoding
        self._sample_rate = vc.sample_rate
        self._speech_rate = self._convert_speed(vc.speed_ratio)
        self._loudness_rate = self._convert_volume(vc.volume_ratio)
        self._pitch = self._convert_pitch(vc.pitch_ratio)
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._session_id = str(uuid.uuid4())

        # 根据音色类型确定 Resource ID
        self._resource_id = self._get_resource_id_for_speaker(self._speaker)

        # ===== Expressive 情绪合成配置 =====
        self._is_cloned = self._speaker.startswith("S_")
        # 复刻音色必须用 expressive；预置音色用配置的模型（默认 expressive 才支持情绪标签）
        self._model = "seed-tts-2.0-expressive" if self._is_cloned else vc.model
        self._emotion_enabled = vc.emotion_enabled
        self._context_text = vc.context_text.strip()

    @property
    def speaker(self) -> str:
        """当前音色 ID（供情绪标签构建判断音色类型）"""
        return self._speaker

    @staticmethod
    def _convert_speed(speed_ratio: float) -> int:
        """旧 speed_ratio (0.5-2.0) → 新 speech_rate (-50~100), 1.0=0"""
        return int((speed_ratio - 1.0) * 100)

    @staticmethod
    def _convert_volume(volume_ratio: float) -> int:
        """旧 volume_ratio (0.5-2.0) → 新 loudness_rate (-50~100), 1.0=0"""
        return int((volume_ratio - 1.0) * 100)

    @staticmethod
    def _convert_pitch(pitch_ratio: float) -> int:
        """旧 pitch_ratio (0.5-2.0) → 新 pitch (-12~12), 1.0=0"""
        if pitch_ratio <= 1.0:
            return int((pitch_ratio - 1.0) * 24)
        else:
            return int((pitch_ratio - 1.0) * 12)

    @staticmethod
    def _get_resource_id_for_speaker(speaker: str) -> str:
        """
        根据音色前缀/后缀匹配 Resource ID（仅支持两种）：
        - S_ 开头 → seed-icl-2.0（声音复刻，控制台训练生成）
        - _uranus_bigtts 结尾 → seed-tts-2.0（预置语音合成，zh_/en_/ja_ 等语种前缀）
        - 其余音色不支持
        """
        if speaker.startswith('S_'):
            return "seed-icl-2.0"
        elif speaker.endswith("_uranus_bigtts"):
            return "seed-tts-2.0"
        else:
            raise ValueError(f"不支持的音色: {speaker}，仅支持 S_ 开头的克隆音色和 _uranus_bigtts 结尾的预置音色")

    def set_cloned_voice(self, voice_id: str):
        """切换音色（设置 speaker + 重新计算 resource_id / model）"""
        if voice_id:
            resource_id = self._get_resource_id_for_speaker(voice_id)
            self._speaker = voice_id
            self._resource_id = resource_id
            self._is_cloned = voice_id.startswith("S_")
            self._model = "seed-tts-2.0-expressive" if self._is_cloned else settings.volcengine.model
        logger.info(f"[Volcengine-TTS] 音色切换: {voice_id or self._speaker}, resource_id={self._resource_id}, model={self._model}")

    def _build_headers(self) -> dict:
        """构建 WebSocket 连接请求头"""
        return {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Control-Require-Usage-Tokens-Return": "*",
        }

    def _build_session_payload(self) -> bytes:
        """构建 StartSession 的 payload

        model 字段（官方要求）：
        - 复刻音色（S_）必须指定 seed-tts-2.0-expressive
        - 预置音色需 expressive 才支持情绪控制，standard 不支持

        context_texts（整体情绪）：
        - 声音复刻2.0（Doubao-Seed-ICL 2.0）已支持 context_texts，故预置音色与
          复刻音色统一在会话级下发「整体情绪」全局语音指令；
        - 不再使用逐句/逐块的 <cot> / {{additions}} 局部情绪标签。
        """
        req_params = {
            "model": self._model,
            "speaker": self._speaker,
            "audio_params": {"format": self._encoding, "sample_rate": self._sample_rate},
        }
        if self._speech_rate != 0:
            req_params["audio_params"]["speech_rate"] = self._speech_rate
        if self._loudness_rate != 0:
            req_params["audio_params"]["loudness_rate"] = self._loudness_rate
        if self._pitch != 0:
            req_params["post_process"] = {"pitch": self._pitch}

        # 整体情绪/语言指令：会话级全局指令，预置音色 + 复刻2.0 音色统一下发
        # 优先用前端运行时配置（可在「配置管理」页填写），未设置则回退到 .env 默认
        try:
            from web.routers.config_router import get_context_text
            context_text = get_context_text()
        except Exception:
            context_text = self._context_text
        if (
            self._emotion_enabled
            and context_text
            and "expressive" in self._model
        ):
            req_params["context_texts"] = [context_text]

        return json.dumps({"req_params": req_params}, ensure_ascii=False).encode("utf-8")

    def _build_task_payload(self, text: str) -> bytes:
        """构建 TaskRequest 的 payload"""
        return json.dumps({"req_params": {"text": text}}, ensure_ascii=False).encode("utf-8")

    def _make_event_message(self, event_type: EventType, session_id: str = "", payload: bytes = b"{}") -> Message:
        """构建一条带 WithEvent 标志的 FullClientRequest 消息"""
        msg = Message(type=MsgType.FullClientRequest, flag=MsgTypeFlagBits.WithEvent)
        msg.event = event_type
        msg.session_id = session_id
        msg.payload = payload
        return msg

    async def synthesize(
        self,
        text: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> list[bytes]:
        """
        通过 V3 双向流式 WebSocket 合成语音

        关键流程（与 tts_demo.py 一致）：
        1. 建立 WebSocket 连接（subprotocols=["bidirection"]）
        2. StartConnection → ConnectionStarted
        3. StartSession → SessionStarted
        4. TaskRequest（发送文本）
        5. FinishSession（告诉服务端文本送完，开始合成）
        6. 循环接收音频帧，直到 SessionFinished
        7. FinishConnection
        """
        audio_chunks: list[bytes] = []
        self._session_id = str(uuid.uuid4())
        session_id = self._session_id

        # 用量统计：火山引擎按字符计费，埋点覆盖所有 TTS 调用路径
        from scheduler.usage_tracker import UsageTracker
        UsageTracker.get_instance().record_tts("volcengine", text)

        try:
            logger.info(
                f"[Volcengine-TTS] 开始 WebSocket 合成, text长度={len(text)}, "
                f"speaker={self._speaker}, resource_id={self._resource_id}, model={self._model}"
            )

            headers = self._build_headers()

            # ===== 建立 WebSocket 连接 =====
            async with websockets.connect(
                self._WS_URL,
                additional_headers=headers,
                subprotocols=["bidirection"],
                ping_interval=None,
                max_size=10 * 1024 * 1024,
            ) as ws:
                self._ws = ws
                log_id = ws.response.headers.get("x-tt-logid", "N/A") if ws.response else "N/A"
                logger.info(f"[Volcengine-TTS] 已连接, Logid: {log_id}")

                # ===== 1. StartConnection =====
                await ws.send(self._make_event_message(EventType.StartConnection).marshal())
                logger.debug("[Volcengine-TTS] 已发送 StartConnection")

                await _wait_for_event(ws, MsgType.FullServerResponse, EventType.ConnectionStarted)
                logger.debug("[Volcengine-TTS] 收到 ConnectionStarted")

                # ===== 2. StartSession =====
                session_payload = self._build_session_payload()
                start_sess_msg = self._make_event_message(EventType.StartSession, session_id, session_payload)
                await ws.send(start_sess_msg.marshal())
                logger.debug(f"[Volcengine-TTS] 已发送 StartSession, speaker={self._speaker}")

                await _wait_for_event(ws, MsgType.FullServerResponse, EventType.SessionStarted)
                logger.debug("[Volcengine-TTS] 收到 SessionStarted")

                # ===== 3. TaskRequest（发送文本） =====
                task_payload = self._build_task_payload(text)
                task_msg = self._make_event_message(EventType.TaskRequest, session_id, task_payload)
                await ws.send(task_msg.marshal())
                logger.debug(f"[Volcengine-TTS] 已发送 TaskRequest, text长度={len(text)}")

                # ===== 4. FinishSession（关键！告诉服务端文本送完了） =====
                finish_sess_msg = self._make_event_message(EventType.FinishSession, session_id, b"{}")
                await ws.send(finish_sess_msg.marshal())
                logger.debug("[Volcengine-TTS] 已发送 FinishSession，等待服务端合成推流...")

                # ===== 5. 接收音频数据 =====
                while True:
                    if cancel_check and cancel_check():
                        logger.warning("[Volcengine-TTS] 收到中断信号，终止合成")
                        break

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        logger.warning(f"[Volcengine-TTS] 接收超时, 已收音频 {sum(len(c) for c in audio_chunks)} 字节")
                        break

                    if isinstance(raw, str):
                        logger.debug(f"[Volcengine-TTS] 文本帧: {raw[:200]!r}")
                        continue

                    msg = Message.from_bytes(raw)

                    if msg.type == MsgType.FullServerResponse:
                        event_name = str(msg.event)
                        logger.debug(f"[Volcengine-TTS] 事件: {event_name}")

                        if msg.event == EventType.TTSSentenceStart:
                            logger.debug(f"[Volcengine-TTS] 合成开始: {msg.payload.decode('utf-8', 'ignore')}")
                        elif msg.event == EventType.TTSResponse:
                            if isinstance(msg.payload, (bytes, bytearray)) and len(msg.payload) > 0:
                                audio_chunks.append(bytes(msg.payload))
                                logger.debug(f"[Volcengine-TTS] 收到音频分片: {len(msg.payload)} bytes")
                        elif msg.event == EventType.TTSSentenceEnd:
                            logger.debug(f"[Volcengine-TTS] 合成结束: {msg.payload.decode('utf-8', 'ignore')}")
                        elif msg.event == EventType.SessionFinished:
                            logger.debug(f"[Volcengine-TTS] 会话结束")
                            break
                        elif msg.event == EventType.SessionFailed:
                            error_text = msg.payload.decode('utf-8', 'ignore')
                            raise SpeechAdapterException(f"会话失败: {error_text}")

                    elif msg.type == MsgType.AudioOnlyServer:
                        if msg.payload:
                            audio_chunks.append(msg.payload)
                            logger.debug(f"[Volcengine-TTS] 收到音频分片(AudioOnly): {len(msg.payload)} bytes")

                    elif msg.type == MsgType.Error:
                        error_text = msg.payload.decode('utf-8', 'ignore')
                        raise SpeechAdapterException(f"服务端错误: code={msg.error_code}, {error_text}")

                # ===== 6. FinishConnection =====
                try:
                    finish_conn_msg = self._make_event_message(EventType.FinishConnection, payload=b"{}")
                    await ws.send(finish_conn_msg.marshal())
                except Exception:
                    pass

            total_size = sum(len(c) for c in audio_chunks)
            logger.info(
                f"[Volcengine-TTS] 合成完成, 音频分片数={len(audio_chunks)}, 总大小={total_size} bytes"
            )

            if total_size == 0:
                raise SpeechAdapterException("TTS 合成完成但未返回任何音频数据")

            return audio_chunks

        except SpeechAdapterException:
            raise
        except websockets.exceptions.InvalidStatusCode as e:
            raise SpeechAdapterException(
                f"WebSocket 鉴权失败 (HTTP {e.status_code}): 请检查 API_KEY 是否正确"
            ) from e
        except websockets.exceptions.ConnectionClosed as e:
            raise SpeechAdapterException(f"WebSocket 连接关闭: {e}") from e
        except Exception as e:
            raise SpeechAdapterException(f"TTS 未知错误: {e}") from e
        finally:
            self._ws = None

    async def cancel(self):
        """主动终止正在执行的语音合成任务（使用协议 CancelSession 二进制帧）"""
        if self._ws:
            logger.info("[Volcengine-TTS] 主动终止 WebSocket 合成")
            try:
                cancel_msg = self._make_event_message(EventType.CancelSession, self._session_id, b"{}")
                await self._ws.send(cancel_msg.marshal())
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def close(self):
        """关闭连接"""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
