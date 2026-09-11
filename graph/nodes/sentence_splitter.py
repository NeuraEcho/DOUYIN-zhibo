"""
分句切割节点 - 以标点符号断句，输出完整短句送入下游 TTS
"""

import re

from loguru import logger

from graph.state import AudioTask, LiveState

# 中英文标点断句字符集合（用于逐字符扫描，比正则更可靠）
_DELIMITER_CHARS = set('。！？；\n.!?;')


def _split_sentences(text: str) -> tuple:
    """
    逐字符扫描文本，按中英文句号/问号/叹号/分号断句。
    返回 (sentences_list, remaining_text)。
    """
    sentences = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _DELIMITER_CHARS:
            sentence = text[start:i + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = i + 1
    remaining = text[start:].strip()
    return sentences, remaining


def sentence_splitter(state: LiveState) -> dict:
    """
    分句切割节点（流式中间件）

    职责：
    - 以标点符号断句，输出完整短句
    - 未完成句子保留在 buffer 继续接收
    - 为每个完整句子创建 AudioTask

    设计为可对接真正的逐 token 流式场景：
    边收 token 边断句，已切割出的完整短句立即送入下游 TTS
    """
    text = state.raw_llm_output + state.text_segment_buffer

    sentences, remaining = _split_sentences(text)

    # 兜底：如果文本很长但没切出任何句子（缺少标点），按 ~80 字强制切割
    if not sentences and len(text) > 80 and not remaining:
        logger.warning(
            f"[sentence_splitter] 文本长度={len(text)} 但未检测到断句标点，"
            f"启用兜底按 80 字强制切割"
        )
        sentences = []
        for j in range(0, len(text), 80):
            chunk = text[j:j + 80].strip()
            if chunk:
                sentences.append(chunk)
        remaining = ""

    # 为每个完整句子创建音频任务
    new_tasks = []
    base_idx = state.sentence_counter
    for i, sent in enumerate(sentences):
        new_tasks.append(AudioTask(
            sentence_index=base_idx + i,
            text=sent,
        ))

    logger.info(
        f"[sentence_splitter] 输入长度={len(text)}, "
        f"切割出 {len(sentences)} 个句子, "
        f"剩余缓冲长度={len(remaining)}, "
        f"累计任务数={len(state.audio_tasks) + len(new_tasks)}"
    )

    # 脚本播报模式：将分句数组注册到 SessionManager，用于断点追踪和精确续接
    if state.bypass_llm and new_tasks and not state.audio_tasks:
        # 首次分句（bypass_llm 模式只有一轮分句），注册完整脚本句子列表
        from scheduler.session_manager import SessionManager
        session_manager = SessionManager.get_instance()
        all_sentences = [t.text for t in new_tasks]
        session_manager.set_script_sentences(all_sentences)

    return {
        "audio_tasks": state.audio_tasks + new_tasks,
        "text_segment_buffer": remaining,
        "sentence_counter": state.sentence_counter + len(sentences),
        "current_text_chunk": sentences[0] if sentences else "",
    }
