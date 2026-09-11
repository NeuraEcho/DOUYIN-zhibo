"""
事件总线单元测试
验证事件发布/消费/监听器逻辑
"""

import asyncio

import pytest

from scheduler.event_bus import EventBus, EventType, LiveEvent


@pytest.fixture
def fresh_event_bus():
    """每个测试使用全新的 EventBus 实例"""
    EventBus._instance = None
    bus = EventBus.get_instance()
    yield bus
    EventBus._instance = None


@pytest.mark.asyncio
async def test_publish_and_consume(fresh_event_bus):
    """测试事件发布和消费"""
    event = LiveEvent(
        event_type=EventType.DANMAKU_MESSAGE,
        source="test",
        content="你好",
    )
    await fresh_event_bus.publish(event)

    consumed = await fresh_event_bus.consume()
    assert consumed.event_type == EventType.DANMAKU_MESSAGE
    assert consumed.source == "test"
    assert consumed.content == "你好"


@pytest.mark.asyncio
async def test_subscribe_listener(fresh_event_bus):
    """测试事件监听器"""
    received = []

    async def handler(event: LiveEvent):
        received.append(event)

    fresh_event_bus.subscribe(EventType.AUTO_BROADCAST, handler)

    event = LiveEvent(
        event_type=EventType.AUTO_BROADCAST,
        source="timer",
        content="定时播报",
    )
    await fresh_event_bus.publish(event)

    assert len(received) == 1
    assert received[0].content == "定时播报"


@pytest.mark.asyncio
async def test_listener_not_triggered_by_other_type(fresh_event_bus):
    """测试监听器不被其他类型事件触发"""
    received = []

    async def handler(event: LiveEvent):
        received.append(event)

    fresh_event_bus.subscribe(EventType.DANMAKU_MESSAGE, handler)

    # 发布不同类型的事件
    event = LiveEvent(
        event_type=EventType.AUTO_BROADCAST,
        source="timer",
        content="定时播报",
    )
    await fresh_event_bus.publish(event)

    assert len(received) == 0


@pytest.mark.asyncio
async def test_clear_queue(fresh_event_bus):
    """测试清空事件队列"""
    event1 = LiveEvent(event_type=EventType.AUTO_BROADCAST, source="t", content="1")
    event2 = LiveEvent(event_type=EventType.DANMAKU_MESSAGE, source="t", content="2")

    await fresh_event_bus.publish(event1)
    await fresh_event_bus.publish(event2)

    fresh_event_bus.clear()

    # 队列应该为空，consume 会阻塞
    # 用 asyncio.wait_for 验证超时（说明队列为空）
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(fresh_event_bus.consume(), timeout=0.1)


def test_live_event_metadata_default():
    """测试 LiveEvent 默认 metadata"""
    event = LiveEvent(
        event_type=EventType.INTERRUPT,
        source="test",
        content="打断",
    )
    assert event.metadata == {}


def test_live_event_custom_metadata():
    """测试 LiveEvent 自定义 metadata"""
    event = LiveEvent(
        event_type=EventType.DANMAKU_MESSAGE,
        source="danmaku",
        content="你好",
        metadata={"user": "test_user"},
    )
    assert event.metadata["user"] == "test_user"
