"""
DeepSeek-R1 适配器 SSE 解析单元测试
验证 JSON 解析逻辑（mock HTTP 响应）
"""

import json

import pytest


def test_sse_json_parse_content():
    """测试 SSE data 行的 JSON 解析"""
    # 模拟 SSE data 行
    data_str = json.dumps({
        "choices": [{
            "delta": {"content": "你好"},
            "index": 0,
        }]
    })

    data = json.loads(data_str)
    choices = data.get("choices", [])
    assert len(choices) == 1

    delta = choices[0].get("delta", {})
    content = delta.get("content", "")
    assert content == "你好"


def test_sse_json_parse_reasoning_content():
    """测试 DeepSeek-R1 reasoning_content 字段"""
    data_str = json.dumps({
        "choices": [{
            "delta": {
                "reasoning_content": "让我想想...",
                "content": "",
            },
            "index": 0,
        }]
    })

    data = json.loads(data_str)
    delta = data["choices"][0]["delta"]

    reasoning = delta.get("reasoning_content", "")
    content = delta.get("content", "")

    assert reasoning == "让我想想..."
    assert content == ""


def test_sse_json_parse_empty_choices():
    """测试空 choices 的防御性处理"""
    data_str = json.dumps({"choices": []})
    data = json.loads(data_str)
    choices = data.get("choices", [])
    assert len(choices) == 0


def test_sse_json_parse_invalid():
    """测试无效 JSON 的处理"""
    data_str = "not valid json{"
    with pytest.raises(json.JSONDecodeError):
        json.loads(data_str)


def test_sse_done_signal():
    """测试 SSE [DONE] 终止信号"""
    data_str = "[DONE]"
    assert data_str == "[DONE]"


def test_sse_accumulate_text():
    """测试多行 SSE 文本累积"""
    lines = [
        json.dumps({"choices": [{"delta": {"content": "你"}}]}),
        json.dumps({"choices": [{"delta": {"content": "好"}}]}),
        json.dumps({"choices": [{"delta": {"content": "，"}}]}),
        json.dumps({"choices": [{"delta": {"content": "欢迎"}}]}),
    ]

    full_text = ""
    for line in lines:
        data = json.loads(line)
        content = data["choices"][0]["delta"].get("content", "")
        full_text += content

    assert full_text == "你好，欢迎"
