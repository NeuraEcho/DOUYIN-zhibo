"""
结束判断节点 - 本轮话术全部处理完毕，重置状态，回到空闲
"""

from loguru import logger

from graph.state import InterruptReason, LiveState, RunMode


def round_complete(state: LiveState) -> dict:
    """
    结束判断与状态重置节点

    职责：
    - 将本轮 AI 输出写入对话记忆
    - 重置运行状态为 IDLE
    - 清理本轮临时状态
    """
    from langchain_core.messages import AIMessage

    if state.error_message:
        logger.warning(f"[round_complete] 因异常结束: {state.error_message}")
        return {"run_mode": RunMode.IDLE, "is_round_complete": True}

    # 将完整输出写入对话记忆
    full_response = " ".join([t.text for t in state.audio_tasks if t.is_complete])
    new_messages = [AIMessage(content=full_response)]

    logger.info(
        f"[round_complete] 本轮完成, "
        f"合成句子数={state.completed_audio_count}, "
        f"回复长度={len(full_response)}"
    )

    return {
        "messages": new_messages,
        "run_mode": RunMode.IDLE,
        "is_round_complete": True,
        "text_segment_buffer": "",
        "current_text_chunk": "",
        "interrupt_reason": InterruptReason.NONE,
        "interrupted_text": "",
    }
