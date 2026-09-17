"""
DeepSeek-V4-Flash 适配器
封装鉴权、SSE 流式解析、请求主动取消
"""

import json
from typing import AsyncIterator, Callable, Optional

import httpx
from loguru import logger

from adapters.base import BaseLLMAdapter
from common.exceptions import DeepSeekAdapterException
from config.settings import settings


class DeepSeekR1Adapter(BaseLLMAdapter):
    """
    DeepSeek-V4-Flash 云端 SSE 流式适配器

    职责：
    - 鉴权封装（API Key + Base URL）
    - 请求体组装
    - SSE 流式解析（逐 token 接收）
    - 提供取消请求能力（中断长连接）
    - 限流保护、重试策略、超时捕获
    """

    def __init__(self):
        self._api_key = settings.deepseek.api_key.strip()
        self._base_url = settings.deepseek.base_url.strip()
        self._model = settings.deepseek.model.strip()
        self._max_tokens = settings.deepseek.max_tokens
        self._temperature = settings.deepseek.temperature
        self._timeout = settings.deepseek.timeout
        self._current_response: Optional[httpx.Response] = None
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._timeout, connect=10.0),
            )
        return self._client

    async def stream_chat(
        self,
        messages: list[dict],
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        调用 DeepSeek-R1 SSE 流式接口

        流程：
        1. 组装请求体（model + messages + stream=true）
        2. 发送 POST 请求，接收 SSE 流
        3. 逐行解析 data: {...} 中的 delta.content
        4. 累积完整文本返回
        5. 支持通过 cancel_check 主动中断
        """
        client = self._get_client()
        full_text = ""

        request_body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stream": True,
        }

        try:
            logger.info(f"[DeepSeek-R1] 开始流式请求, model={self._model}")

            async with client.stream("POST", "/chat/completions", json=request_body) as response:
                self._current_response = response
                response.raise_for_status()

                async for line in response.aiter_lines():
                    # 检查是否需要中断
                    if cancel_check and cancel_check():
                        logger.warning("[DeepSeek-R1] 收到中断信号，终止流式请求")
                        break

                    # SSE 格式解析
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    # 解析 SSE JSON 数据
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})

                        # DeepSeek-R1 的 reasoning_content（思考过程）仅记录日志
                        reasoning = delta.get("reasoning_content", "")
                        if reasoning:
                            logger.debug(f"[DeepSeek-R1] reasoning: {reasoning[:80]}...")

                        # 提取实际输出 content
                        content = delta.get("content", "")
                        if content:
                            full_text += content

                    except json.JSONDecodeError:
                        logger.warning(f"[DeepSeek-R1] SSE JSON 解析失败: {data_str[:100]}")
                        continue

            logger.info(f"[DeepSeek-R1] 流式请求完成, 输出长度={len(full_text)}")
            # 用量统计：记录本次 LLM 输入/输出字符（token 由字符估算）
            from scheduler.usage_tracker import UsageTracker
            _in_text = "".join(m.get("content", "") for m in messages)
            UsageTracker.get_instance().record_llm(self._model, _in_text, full_text)
            return full_text

        except httpx.TimeoutException as e:
            raise DeepSeekAdapterException(f"请求超时: {e}") from e
        except httpx.HTTPStatusError as e:
            raise DeepSeekAdapterException(f"HTTP 错误 {e.response.status_code}: {e}") from e
        except Exception as e:
            raise DeepSeekAdapterException(f"未知错误: {e}") from e

    async def stream_chat_iterator(
        self,
        messages: list[dict],
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncIterator[str]:
        """
        真流式迭代器——逐 token yield，不等全部输出完毕。
        供 StreamingPipeline 消费，实现 LLM→TTS 流水线。
        """
        client = self._get_client()

        request_body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stream": True,
        }

        try:
            out_buf = ""  # 累计输出字符，供用量统计
            logger.info(f"[DeepSeek-R1] 开始真流式请求, model={self._model}")

            async with client.stream("POST", "/chat/completions", json=request_body) as response:
                self._current_response = response
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if cancel_check and cancel_check():
                        logger.warning("[DeepSeek-R1] 流式迭代器收到中断信号")
                        break

                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})

                        # reasoning_content 不产出
                        reasoning = delta.get("reasoning_content", "")
                        if reasoning:
                            logger.debug(f"[DeepSeek-R1] reasoning (skipped): {reasoning[:80]}...")

                        content = delta.get("content", "")
                        if content:
                            out_buf += content
                            yield content

                    except json.JSONDecodeError:
                        logger.warning(f"[DeepSeek-R1] SSE JSON 解析失败: {data_str[:100]}")
                        continue

            # 用量统计：记录本次流式 LLM 输入/输出字符
            from scheduler.usage_tracker import UsageTracker
            _in_text = "".join(m.get("content", "") for m in messages)
            UsageTracker.get_instance().record_llm(self._model, _in_text, out_buf)
            logger.info("[DeepSeek-R1] 流式迭代器完成")

        except httpx.TimeoutException as e:
            raise DeepSeekAdapterException(f"请求超时: {e}") from e
        except httpx.HTTPStatusError as e:
            raise DeepSeekAdapterException(f"HTTP 错误 {e.response.status_code}: {e}") from e
        except Exception as e:
            raise DeepSeekAdapterException(f"未知错误: {e}") from e

    async def cancel(self):
        """主动取消正在进行的流式请求"""
        if self._current_response:
            logger.info("[DeepSeek-R1] 主动取消请求")
            # httpx 的 Response 关闭即中断连接
            await self._current_response.aclose()
            self._current_response = None

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
