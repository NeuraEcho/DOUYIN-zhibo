"""
分句切割节点单元测试
验证标点断句逻辑和 AudioTask 创建
"""

import pytest

from graph.nodes.sentence_splitter import sentence_splitter, SENTENCE_DELIMITERS
from graph.state import LiveState


def test_sentence_splitter_basic():
    """测试基本分句切割"""
    state = LiveState(thread_id="test")
    state.raw_llm_output = "你好，欢迎来到直播间！今天给大家带来好东西。"
    state.text_segment_buffer = ""

    result = sentence_splitter(state)

    # 逗号不在断句正则中，所以“你好，”不会单独切分
    # 断句点为：！和。
    assert len(result["audio_tasks"]) == 2
    assert result["audio_tasks"][0].text == "你好，欢迎来到直播间！"
    assert result["audio_tasks"][1].text == "今天给大家带来好东西。"
    assert result["sentence_counter"] == 2


def test_sentence_splitter_with_buffer():
    """测试带缓冲的分句切割（未完成的句子留在 buffer）"""
    state = LiveState(thread_id="test")
    state.raw_llm_output = "第一句话。第二句话还没"
    state.text_segment_buffer = "结束"

    result = sentence_splitter(state)

    # "第一句话。" 是完整句子
    assert len(result["audio_tasks"]) == 1
    assert result["audio_tasks"][0].text == "第一句话。"
    # "第二句话还没结束" 留在 buffer
    assert result["text_segment_buffer"] == "第二句话还没结束"


def test_sentence_splitter_empty():
    """测试空文本"""
    state = LiveState(thread_id="test")
    state.raw_llm_output = ""
    state.text_segment_buffer = ""

    result = sentence_splitter(state)

    assert len(result["audio_tasks"]) == 0
    assert result["sentence_counter"] == 0


def test_sentence_delimiters_regex():
    """测试断句正则匹配"""
    text = "你好！世界。测试？结束；"
    matches = list(SENTENCE_DELIMITERS.finditer(text))
    assert len(matches) == 4
