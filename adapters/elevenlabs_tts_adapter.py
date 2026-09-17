"""
ElevenLabs TTS 适配器（REST 流式接口 /v1/text-to-speech/{voice_id}/stream）

为什么不用官方 SDK：
- 官方 Python SDK 是同步阻塞实现，本项目全链路 asyncio（AudioPlayer 实时节流、
  QAInserter 后台预生成、LangGraph 节点都在同一事件循环），同步调用会阻塞播放时钟；
- 用户给的 SDK 文档链接指向 speech-engine（实时语音对话引擎），与 TTS 不是一回事。
故直接用已在依赖中的 httpx 走 REST 流式接口，零新增依赖，风格对齐 speech_adapter.py。

音频契约：
- output_format 必须是 pcm_*，默认 pcm_24000（24kHz / 16-bit / mono），与 AUDIO_SAMPLE_RATE 对齐；
  非 PCM 会让 AudioPlayer 的 np.frombuffer(data, np.int16) 输出刺耳噪声。
- ElevenLabs 按网络包切流，末尾可能出现奇数字节，int16 要求偶数对齐，此处做跨包 carry 处理。
"""

import asyncio
import json
from typing import Any, Callable, Optional

import httpx
from loguru import logger

from adapters.base import BaseTTSAdapter
from common.exceptions import ElevenLabsAdapterException
from config.settings import settings

# output_format 中合法的 PCM 采样率（官方枚举）
_PCM_SAMPLE_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)

# multilingual_v2 系列官方明确不支持 language_code，下发会被忽略，故不发送
_NO_LANGUAGE_CODE_MODELS = ("eleven_multilingual_v2",)


class _RetryableError(ElevenLabsAdapterException):
    """瞬时错误（429 限流 / 5xx / 网络抖动），可按退避策略重试"""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after


class ElevenLabsTTSAdapter(BaseTTSAdapter):
    """
    ElevenLabs 云端流式 TTS 适配器

    职责：
    - 封装 /v1/text-to-speech/{voice_id}/stream 流式请求
    - 输出 16-bit 单声道裸 PCM 分片，保证偶数字节对齐
    - 支持运行时切换音色（与 MiniMax / 火山适配器同一 set_cloned_voice 契约）
    - 429 / 5xx / 网络抖动按 Retry-After 或指数退避重试
    - 支持 cancel_check 与主动 cancel 中断合成
    """

    def __init__(self):
        el = settings.elevenlabs
        self._api_key = el.api_key.strip()
        self._base_url = el.base_url.strip().rstrip("/")
        self._default_voice = el.voice.strip()
        self._env_model_id = el.model_id.strip()
        self._language_code = el.language_code.strip()
        self._output_format = el.output_format.strip().lower()
        self._sample_rate = el.sample_rate
        self._timeout = el.timeout
        self._connect_timeout = el.connect_timeout
        self._max_retries = max(0, int(el.max_retries))
        self._apply_text_normalization = (el.apply_text_normalization or "auto").strip().lower()
        self._voice_settings = {
            "stability": el.stability,
            "similarity_boost": el.similarity_boost,
            "style": el.style,
            "use_speaker_boost": el.use_speaker_boost,
            "speed": el.speed,
        }

        self._client: Optional[httpx.AsyncClient] = None
        self._current_response: Optional[httpx.Response] = None
        # 运行时选定的音色 ID（由 preview / voice_clone 路由写入，空则用 .env 默认）
        self._cloned_voice_id: str = ""

        self._warn_config()

    # ================= 配置与音色 =================

    def _warn_config(self):
        """构造期只告警不抛异常：create_tts_adapter() 在部分调用点位于 try 之外，
        抛异常会击穿 LangGraph 节点；真正的硬拦放在 synthesize() 的 _ensure_ready()。"""
        if not self._api_key:
            logger.warning("[ElevenLabs] ELEVENLABS_API_KEY 未配置，合成时会直接报错")
        if not self._output_format.startswith("pcm_"):
            logger.error(
                f"[ElevenLabs] ELEVENLABS_OUTPUT_FORMAT={self._output_format} 非法，必须是 pcm_*；"
                f"非 PCM 格式会让 AudioPlayer 输出噪声"
            )
        elif self._sample_rate not in _PCM_SAMPLE_RATES:
            logger.error(f"[ElevenLabs] 采样率 {self._sample_rate} 不在官方支持列表内")
        elif self._sample_rate != settings.audio.sample_rate:
            logger.warning(
                f"[ElevenLabs] 采样率不一致：output_format={self._output_format}"
                f"({self._sample_rate}Hz) 但 AUDIO_SAMPLE_RATE={settings.audio.sample_rate}Hz，"
                f"播放会变调变速，请把两者统一（推荐都用 24000）"
            )
        if self._sample_rate == 44100:
            logger.warning("[ElevenLabs] pcm_44100 需要 Pro 及以上套餐，否则返回 403")

    def set_cloned_voice(self, voice_id: str):
        """设置音色 ID（与 MiniMax / 火山适配器同名同义），空串表示回退 .env 默认音色"""
        self._cloned_voice_id = (voice_id or "").strip()
        logger.info(
            f"[ElevenLabs] 音色切换: {self._cloned_voice_id or self._default_voice or '（未设置）'}"
        )

    def _get_active_voice(self) -> str:
        return self._cloned_voice_id or self._default_voice

    def _get_active_model(self) -> str:
        """模型优先取前端运行时热更新值，未设置则回退 .env（与火山适配器读 context_text 同范式）"""
        try:
            from web.routers.config_router import get_elevenlabs_model
            return get_elevenlabs_model() or self._env_model_id
        except Exception:
            return self._env_model_id

    def _get_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端（ElevenLabs 鉴权走 xi-api-key 请求头，不是 Bearer）"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/octet-stream",
                },
                timeout=httpx.Timeout(self._timeout, connect=self._connect_timeout),
            )
        return self._client

    # ================= 请求构造 =================

    def _build_body(self, text: str, previous_text: str = "", next_text: str = "") -> dict[str, Any]:
        model_id = self._get_active_model()
        body: dict[str, Any] = {
            "text": text,
            "model_id": model_id,
            "voice_settings": dict(self._voice_settings),
            "apply_text_normalization": self._apply_text_normalization,
        }
        # multilingual_v2 官方明确不支持 language_code，其余模型按需下发
        if self._language_code and not any(m in model_id for m in _NO_LANGUAGE_CODE_MODELS):
            body["language_code"] = self._language_code
        # 前后文用于改善分句拼接处的语调连贯；调用方不传就不下发
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        return body

    def _ensure_ready(self, text: str):
        """合成前置校验：把配置错误翻译成可读中文提示，而不是让人对着 HTTP 401 猜"""
        if not text or not text.strip():
            raise ElevenLabsAdapterException("待合成文本为空")
        if not self._api_key:
            raise ElevenLabsAdapterException(
                "ELEVENLABS_API_KEY 未配置：请在 .env 填入后重启 python main.py"
            )
        if not self._get_active_voice():
            raise ElevenLabsAdapterException(
                "未指定 ElevenLabs 音色：请在「音色克隆」页选一个，或在 .env 填 ELEVENLABS_VOICE"
            )
        if not self._output_format.startswith("pcm_"):
            raise ElevenLabsAdapterException(
                f"ELEVENLABS_OUTPUT_FORMAT 必须是 pcm_* 格式（当前 {self._output_format}）。"
                f"项目音频链路按 16-bit 单声道裸 PCM 消费，mp3/opus 会变成噪声，推荐 pcm_24000"
            )
        if self._sample_rate != settings.audio.sample_rate:
            raise ElevenLabsAdapterException(
                f"采样率不一致：ELEVENLABS_OUTPUT_FORMAT={self._output_format}"
                f"（{self._sample_rate}Hz）但 AUDIO_SAMPLE_RATE={settings.audio.sample_rate}Hz，"
                f"播放会变调变速，请统一后再试（推荐都用 24000）"
            )

    # ================= 错误解析 =================

    @staticmethod
    def _extract_error_message(status_code: int, raw: str) -> str:
        """把 ElevenLabs 错误体解析成可读中文提示

        官方错误体有三种形态：
        {"detail": {"status": "...", "message": "..."}}
        {"detail": [{"loc": [...], "msg": "...", "type": "..."}]}   # 422 参数校验
        {"detail": "纯字符串"}
        """
        detail_msg = ""
        try:
            payload = json.loads(raw)
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            if isinstance(detail, str):
                detail_msg = detail.strip()
            elif isinstance(detail, dict):
                detail_msg = str(detail.get("message") or detail.get("status") or "").strip()
            elif isinstance(detail, list) and detail:
                first = detail[0]
                detail_msg = str(first.get("msg") or first) if isinstance(first, dict) else str(first)
        except Exception:
            detail_msg = (raw or "").strip()[:200]

        if status_code >= 500:
            hint = "ElevenLabs 服务端错误，稍后重试"
        else:
            hint = {
                400: "请求参数有误",
                401: "API Key 无效或未配置（检查 .env 的 ELEVENLABS_API_KEY）",
                403: "无权限：该模型或输出格式需要更高的订阅套餐",
                404: "voice_id 不存在（到「音色克隆」页刷新在线列表后重选）",
                422: "参数校验失败（常见：model_id 该套餐不可用、text 超长）",
                429: "配额或并发超限：本月字符额度用尽，或同时在跑的合成请求数超过套餐并发上限",
            }.get(status_code, "")

        return " | ".join(p for p in (f"HTTP {status_code}", hint, detail_msg) if p)

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float:
        raw = response.headers.get("retry-after", "")
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 0.0

    # ================= 合成主流程 =================

    async def synthesize(
        self,
        text: str,
        cancel_check: Optional[Callable[[], bool]] = None,
        previous_text: str = "",
        next_text: str = "",
    ) -> list[bytes]:
        """
        调用 ElevenLabs 流式 TTS，返回 PCM 分片列表

        :param text: 待合成文本
        :param cancel_check: 取消检查回调，返回 True 立即中断
        :param previous_text: 上一句文本（可选，改善分句拼接处的语调连贯）
        :param next_text: 下一句文本（可选）
        """
        self._ensure_ready(text)

        # 用量统计：ElevenLabs 按字符计费（1 字符 = 1 credit），埋点覆盖所有 TTS 调用路径
        from scheduler.usage_tracker import UsageTracker
        UsageTracker.get_instance().record_tts("elevenlabs", text)

        voice = self._get_active_voice()
        logger.info(
            f"[ElevenLabs] 开始 TTS 合成, text长度={len(text)}, voice={voice}, "
            f"model={self._get_active_model()}, format={self._output_format}"
        )

        attempt = 0
        while True:
            try:
                return await self._synthesize_once(
                    text, voice, cancel_check, previous_text, next_text
                )
            except _RetryableError as e:
                if cancel_check and cancel_check():
                    raise ElevenLabsAdapterException("合成被中断") from e
                if attempt >= self._max_retries:
                    raise ElevenLabsAdapterException(
                        f"{e.message}（已重试 {attempt} 次仍失败）"
                    ) from e
                # 优先遵循服务端 Retry-After，否则指数退避，单次上限 10s
                backoff = (
                    min(e.retry_after, 10.0) if e.retry_after > 0
                    else min(2.0 * (2 ** attempt), 10.0)
                )
                backoff = max(backoff, 0.5)
                attempt += 1
                logger.warning(
                    f"[ElevenLabs] 瞬时失败，{backoff:.1f}s 后重试"
                    f"({attempt}/{self._max_retries}): {e.message}"
                )
                await asyncio.sleep(backoff)

    async def _synthesize_once(
        self,
        text: str,
        voice: str,
        cancel_check: Optional[Callable[[], bool]],
        previous_text: str,
        next_text: str,
    ) -> list[bytes]:
        client = self._get_client()
        url = f"{self._base_url}/v1/text-to-speech/{voice}/stream"
        params = {"output_format": self._output_format}
        body = self._build_body(text, previous_text, next_text)

        audio_chunks: list[bytes] = []
        carry = b""   # 跨网络包的半字节余量：int16 必须偶数字节对齐
        total = 0

        try:
            async with client.stream("POST", url, params=params, json=body) as response:
                self._current_response = response

                if response.status_code != 200:
                    raw = (await response.aread()).decode("utf-8", "replace")
                    msg = self._extract_error_message(response.status_code, raw)
                    logger.error(f"[ElevenLabs] 合成失败: {msg}")
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _RetryableError(msg, self._parse_retry_after(response))
                    raise ElevenLabsAdapterException(msg)

                # 200 但 content-type 是 json = 业务错误（额度耗尽 / 内容审核拦截等）
                content_type = response.headers.get("content-type", "")
                if "json" in content_type:
                    raw = (await response.aread()).decode("utf-8", "replace")
                    msg = self._extract_error_message(200, raw) or "ElevenLabs 返回了非音频响应"
                    logger.error(f"[ElevenLabs] 返回非音频响应: {msg}")
                    raise ElevenLabsAdapterException(msg)

                async for chunk in response.aiter_bytes():
                    if cancel_check and cancel_check():
                        logger.warning("[ElevenLabs] 收到中断信号，终止 TTS 合成")
                        break
                    if not chunk:
                        continue
                    carry += chunk
                    # 奇数字节留一个到下一包，保证每个分片都能被 np.int16 正确解析
                    if len(carry) % 2:
                        emit, carry = carry[:-1], carry[-1:]
                    else:
                        emit, carry = carry, b""
                    if emit:
                        audio_chunks.append(emit)
                        total += len(emit)

        except (_RetryableError, ElevenLabsAdapterException):
            raise
        except httpx.TimeoutException as e:
            raise _RetryableError(
                f"ElevenLabs 请求超时（{self._timeout}s）: {type(e).__name__}"
            ) from e
        except httpx.HTTPStatusError as e:
            raise ElevenLabsAdapterException(
                f"ElevenLabs HTTP 错误 {e.response.status_code}"
            ) from e
        except httpx.HTTPError as e:
            # 建连失败 / 连接被重置：国内直连 api.elevenlabs.io 的典型表现
            raise _RetryableError(
                f"连不上 ElevenLabs（{self._base_url}）: {type(e).__name__}: {e}。"
                f"国内网络通常需要代理，可把 ELEVENLABS_BASE_URL 改成可用的中转地址"
            ) from e
        finally:
            self._current_response = None

        if carry:
            logger.warning(
                f"[ElevenLabs] 流末尾有 {len(carry)} 字节奇数残余，已丢弃（int16 需偶数对齐）"
            )

        duration = total / max(1, self._sample_rate * 2)
        logger.info(
            f"[ElevenLabs] TTS 合成完成, 分片数={len(audio_chunks)}, "
            f"总大小={total} bytes, 时长≈{duration:.2f}s"
        )

        if total == 0:
            raise ElevenLabsAdapterException("ElevenLabs 合成完成但未返回任何音频数据")

        return audio_chunks

    async def cancel(self):
        """主动终止正在执行的语音合成任务"""
        if self._current_response:
            logger.info("[ElevenLabs] 主动终止 TTS 合成")
            await self._current_response.aclose()
            self._current_response = None

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
