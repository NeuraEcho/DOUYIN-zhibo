"""
Speech-2.8-Turbo TTS 适配器（MiniMax 原生 API）
封装流式 TTS 请求，接收短句文本，返回流式音频二进制块
支持克隆音色（voice_id）切换
"""

import json
from typing import Callable, Optional

import httpx
from loguru import logger

from adapters.base import BaseTTSAdapter
from common.exceptions import SpeechAdapterException
from config.settings import settings


class Speech28TurboAdapter(BaseTTSAdapter):
    """
    Speech-2.8-Turbo 云端流式 TTS 适配器（MiniMax 原生 API）

    职责：
    - 封装 MiniMax T2A v2 流式 TTS 请求
    - 接收短句文本，返回流式音频二进制块
    - 支持克隆音色（voice_id）动态切换
    - 支持主动终止正在执行的语音合成任务
    - 限流保护、重试策略、超时捕获
    """

    # MiniMax T2A v2 原生 API 地址
    _MINIMAX_TTS_URL = "https://api.minimax.cn/v1/t2a_v2"

    def __init__(self):
        self._api_key = settings.speech.api_key.strip()
        self._model = settings.speech.model.strip()
        self._default_voice = settings.speech.voice.strip()
        self._speed = settings.speech.speed
        self._pitch = getattr(settings.speech, 'pitch', -1)
        self._emotion = getattr(settings.speech, 'emotion', 'happy')
        self._sample_rate = settings.speech.sample_rate
        self._current_response: Optional[httpx.Response] = None
        self._client: Optional[httpx.AsyncClient] = None
        # 克隆音色 ID（运行时可动态切换）
        self._cloned_voice_id: str = ""

    def set_cloned_voice(self, voice_id: str):
        """设置克隆音色 ID，空字符串表示使用默认音色"""
        self._cloned_voice_id = voice_id
        logger.info(f"[Speech-2.8] 音色切换: {voice_id or '默认音色'}")

    def _get_active_voice(self) -> str:
        """获取当前生效的音色 ID"""
        if self._cloned_voice_id:
            return self._cloned_voice_id
        return self._default_voice

    def _get_active_speed(self) -> float:
        """获取当前生效的语速：克隆音色用 1.0（保持原始语速），系统音色用配置值"""
        if self._cloned_voice_id:
            return 1.0  # 克隆音色保持原始语速，不做调整
        return self._speed

    def _get_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        return self._client

    async def synthesize(
        self,
        text: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> list[bytes]:
        """
        调用 MiniMax T2A v2 流式 TTS 接口

        流程：
        1. 组装 MiniMax 原生请求体（model + text + voice_setting + audio_setting）
        2. 发送 POST 请求，接收流式音频
        3. 逐块收集音频二进制数据
        4. 支持通过 cancel_check 主动终止
        """
        client = self._get_client()
        audio_chunks: list[bytes] = []
        active_voice = self._get_active_voice()

        # 用量统计：MiniMax 按字符计费（官方规则 1 汉字=2 字符），埋点覆盖所有 TTS 调用路径
        from scheduler.usage_tracker import UsageTracker
        UsageTracker.get_instance().record_tts("minimax", text)

        # MiniMax T2A v2 原生请求格式（真人感优化参数）
        active_speed = self._get_active_speed()
        request_body = {
            "model": self._model,
            "text": text,
            "stream": True,
            "voice_setting": {
                "voice_id": active_voice,
                "speed": active_speed,     # 克隆音色=1.0，系统音色=配置值
                "vol": 1.0,
                "pitch": self._pitch,        # -1~-2 略降音调更真人
                "emotion": self._emotion,    # happy/surprised/calm
            },
            "audio_setting": {
                "sample_rate": self._sample_rate,
                "bitrate": 128000,
                "format": "pcm",
                "channel": 1,
            },
            "text_normalization": True,  # 数字/金额规范化，价格读得更自然
        }

        try:
            logger.info(
                f"[Speech-2.8] 开始 TTS 合成, text长度={len(text)}, "
                f"voice={active_voice}{'(克隆)' if self._cloned_voice_id else ''}"
            )

            async with client.stream(
                "POST", self._MINIMAX_TTS_URL, json=request_body
            ) as response:
                self._current_response = response
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")

                # SSE 流式响应：逐行解析 data: {"data":{"audio":"<hex>"}}
                if "event-stream" in content_type:
                    async for line in response.aiter_lines():
                        # 检查是否需要中断
                        if cancel_check and cancel_check():
                            logger.warning("[Speech-2.8] 收到中断信号，终止 TTS 合成")
                            break

                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue

                        # 提取 data: 后面的 JSON
                        json_str = line[5:].strip()
                        if not json_str:
                            continue

                        try:
                            event = json.loads(json_str)
                        except json.JSONDecodeError:
                            continue

                        # 先提取 hex 编码的音频数据（事件可能同时包含 audio 和 base_resp）
                        data = event.get("data", {})
                        audio_hex = data.get("audio", "")
                        if audio_hex:
                            try:
                                audio_bytes = bytes.fromhex(audio_hex)
                                audio_chunks.append(audio_bytes)
                            except ValueError:
                                pass  # 非 hex 数据，跳过

                        # 再检查是否为错误响应（status_code != 0 才是错误）
                        if "base_resp" in event:
                            status_code = event["base_resp"].get("status_code", 0)
                            if status_code != 0:
                                status_msg = event["base_resp"].get("status_msg", "未知错误")
                                raise SpeechAdapterException(
                                    f"MiniMax TTS 错误 {status_code}: {status_msg}"
                                )

                else:
                    # 非 SSE 响应（兼容旧格式或错误响应）
                    async for chunk in response.aiter_bytes():
                        if cancel_check and cancel_check():
                            logger.warning("[Speech-2.8] 收到中断信号，终止 TTS 合成")
                            break

                        # 检测是否为 JSON 错误响应
                        try:
                            err_data = json.loads(chunk)
                            if "base_resp" in err_data:
                                status_code = err_data["base_resp"].get("status_code", 0)
                                status_msg = err_data["base_resp"].get("status_msg", "未知错误")
                                raise SpeechAdapterException(
                                    f"MiniMax TTS 错误 {status_code}: {status_msg}"
                                )
                        except json.JSONDecodeError:
                            pass

                        if chunk:
                            audio_chunks.append(chunk)

            total_size = sum(len(c) for c in audio_chunks)
            logger.info(
                f"[Speech-2.8] TTS 合成完成, "
                f"音频分片数={len(audio_chunks)}, "
                f"总大小={total_size} bytes"
            )

            if total_size == 0:
                raise SpeechAdapterException("TTS 合成完成但未返回任何音频数据")

            return audio_chunks

        except httpx.TimeoutException as e:
            raise SpeechAdapterException(f"TTS 请求超时: {e}") from e
        except httpx.HTTPStatusError as e:
            raise SpeechAdapterException(f"TTS HTTP 错误 {e.response.status_code}: {e}") from e
        except Exception as e:
            raise SpeechAdapterException(f"TTS 未知错误: {e}") from e

    async def cancel(self):
        """主动终止正在执行的语音合成任务"""
        if self._current_response:
            logger.info("[Speech-2.8] 主动终止 TTS 合成")
            await self._current_response.aclose()
            self._current_response = None

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
