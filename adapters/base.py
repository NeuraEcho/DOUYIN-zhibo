"""
适配器基类 - 定义云端 AI 模型的统一接口规范
插件适配器模式：模型切换只修改此层，上层业务代码不变
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, Optional


class BaseLLMAdapter(ABC):
    """LLM 适配器统一接口"""

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict],
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> str:
        """
        流式对话接口
        :param messages: 对话消息列表
        :param cancel_check: 取消检查回调，返回 True 表示需要中断
        :return: 完整的模型输出文本
        """
        ...

    @abstractmethod
    async def cancel(self):
        """主动取消正在进行的流式请求"""
        ...

    async def stream_chat_iterator(
        self,
        messages: list[dict],
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncIterator[str]:
        """
        流式对话迭代器接口——逐 token 产出，供流式管线消费。
        默认实现：调用 stream_chat 后逐字符产出（兼容不支持真流式的适配器）。
        支持真流式的适配器应 override 此方法。
        """
        full_text = await self.stream_chat(messages, cancel_check)
        for ch in full_text:
            yield ch


class BaseTTSAdapter(ABC):
    """TTS 适配器统一接口"""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> list[bytes]:
        """
        流式语音合成接口
        :param text: 待合成文本
        :param cancel_check: 取消检查回调
        :return: 音频二进制分片列表
        """
        ...

    @abstractmethod
    async def cancel(self):
        """主动终止正在执行的语音合成任务"""
        ...
