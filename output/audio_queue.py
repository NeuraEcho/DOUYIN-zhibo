"""
音频播放缓冲队列（FIFO）
接收 TTS 适配器推送的音频分片，按序消费播放
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


@dataclass(order=True)
class AudioChunk:
    """带序号的音频分片，保证有序消费"""
    sentence_index: int
    chunk_index: int = field(compare=True)
    data: bytes = field(compare=False)


class AudioQueueService:
    """
    音频播放缓冲队列（单例，FIFO）

    职责：
    - 接收 TTS 适配器推送的音频分片
    - 按序号有序消费（保证句子播放顺序）
    - 打断信号触发时直接清空队列
    - 与 LangGraph 完全解耦的独立异步音频服务
    """

    _instance: Optional["AudioQueueService"] = None

    def __init__(self):
        self._queue: asyncio.PriorityQueue[AudioChunk] = asyncio.PriorityQueue()
        self._chunk_counter: int = 0

    @classmethod
    def get_instance(cls) -> "AudioQueueService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def enqueue(self, audio_data: bytes, sentence_index: int):
        """
        入队音频分片

        :param audio_data: 音频二进制数据
        :param sentence_index: 所属句子序号（保证有序）
        """
        chunk = AudioChunk(
            sentence_index=sentence_index,
            chunk_index=self._chunk_counter,
            data=audio_data,
        )
        self._chunk_counter += 1
        await self._queue.put(chunk)

    async def dequeue(self) -> Optional[AudioChunk]:
        """出队下一个音频分片（阻塞等待）"""
        return await self._queue.get()

    async def clear(self):
        """清空队列（打断时调用）"""
        count = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break
        logger.info(f"[AudioQueue] 队列已清空, 丢弃 {count} 个分片")

    @property
    def size(self) -> int:
        return self._queue.qsize()

    @property
    def is_empty(self) -> bool:
        return self._queue.empty()
