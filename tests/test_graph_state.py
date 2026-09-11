"""
LangGraph State 单元测试
验证状态定义和基本操作
"""

import pytest

from graph.state import AudioTask, InterruptReason, LiveState, RunMode


def test_live_state_defaults():
    """测试 LiveState 默认值"""
    state = LiveState(thread_id="test_room_001")

    assert state.thread_id == "test_room_001"
    assert state.run_mode == RunMode.IDLE
    assert state.messages == []
    assert state.system_prompt == ""
    assert state.raw_llm_output == ""
    assert state.text_segment_buffer == ""
    assert state.sentence_counter == 0
    assert state.audio_tasks == []
    assert state.completed_audio_count == 0
    assert state.interrupt_reason == InterruptReason.NONE
    assert state.is_round_complete is False
    assert state.error_message is None
    assert state.context_window_size == 10


def test_audio_task():
    """测试 AudioTask 数据结构"""
    task = AudioTask(sentence_index=0, text="你好，欢迎来到直播间！")

    assert task.sentence_index == 0
    assert task.text == "你好，欢迎来到直播间！"
    assert task.audio_chunks == []
    assert task.is_complete is False


def test_run_mode_enum():
    """测试运行模式枚举"""
    assert RunMode.IDLE.value == "idle"
    assert RunMode.SCRIPT_AUTO.value == "script_auto"
    assert RunMode.DANMAKU_REPLY.value == "danmaku_reply"
    assert RunMode.MANUAL_INPUT.value == "manual_input"


def test_interrupt_reason_enum():
    """测试打断原因枚举"""
    assert InterruptReason.NONE.value == "none"
    assert InterruptReason.DANMAKU_INTERRUPT.value == "danmaku_interrupt"
    assert InterruptReason.MANUAL_ABORT.value == "manual_abort"
