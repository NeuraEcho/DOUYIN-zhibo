"""
音频队列单元测试
验证有序入队/出队/清空逻辑
"""

import asyncio

import pytest

from output.audio_queue import AudioChunk, AudioQueueService


@pytest.fixture
def fresh_queue():
    """每个测试使用全新的 AudioQueueService 实例"""
    AudioQueueService._instance = None
    queue = AudioQueueService.get_instance()
    yield queue
    AudioQueueService._instance = None


@pytest.mark.asyncio
async def test_enqueue_dequeue_order(fresh_queue):
    """测试入队出队顺序（PriorityQueue 按 sentence_index 排序）"""
    # 按乱序入队
    await fresh_queue.enqueue(b"audio_2", sentence_index=2)
    await fresh_queue.enqueue(b"audio_0", sentence_index=0)
    await fresh_queue.enqueue(b"audio_1", sentence_index=1)

    # PriorityQueue 按 (sentence_index, chunk_index) 排序出队
    chunk0 = await fresh_queue.dequeue()
    chunk1 = await fresh_queue.dequeue()
    chunk2 = await fresh_queue.dequeue()

    assert chunk0.sentence_index == 0  # 最小 sentence_index 先出
    assert chunk1.sentence_index == 1
    assert chunk2.sentence_index == 2


@pytest.mark.asyncio
async def test_queue_size(fresh_queue):
    """测试队列大小"""
    assert fresh_queue.size == 0
    assert fresh_queue.is_empty is True

    await fresh_queue.enqueue(b"audio_0", sentence_index=0)
    assert fresh_queue.size == 1
    assert fresh_queue.is_empty is False

    await fresh_queue.enqueue(b"audio_1", sentence_index=1)
    assert fresh_queue.size == 2


@pytest.mark.asyncio
async def test_queue_clear(fresh_queue):
    """测试清空队列"""
    await fresh_queue.enqueue(b"audio_0", sentence_index=0)
    await fresh_queue.enqueue(b"audio_1", sentence_index=1)
    await fresh_queue.enqueue(b"audio_2", sentence_index=2)

    assert fresh_queue.size == 3

    await fresh_queue.clear()

    assert fresh_queue.size == 0
    assert fresh_queue.is_empty is True


def test_audio_chunk_ordering():
    """测试 AudioChunk 排序逻辑"""
    c1 = AudioChunk(sentence_index=0, chunk_index=0, data=b"a")
    c2 = AudioChunk(sentence_index=0, chunk_index=1, data=b"b")
    c3 = AudioChunk(sentence_index=1, chunk_index=2, data=b"c")

    # 按 (sentence_index, chunk_index) 排序
    chunks = sorted([c3, c1, c2])
    assert chunks[0].data == b"a"
    assert chunks[1].data == b"b"
    assert chunks[2].data == b"c"
