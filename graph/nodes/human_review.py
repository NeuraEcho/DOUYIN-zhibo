"""
人工审核节点 - LangGraph 原生 interrupt_before 人工拦截
双中断机制中的 LangGraph interrupt（节点边界优雅暂停）
"""

from loguru import logger

from graph.state import LiveState


def human_review_gate(state: LiveState) -> dict:
    """
    人工审核节点

    职责：
    - 仅在 is_need_human_review=True 时触发
    - 使用 LangGraph 原生 interrupt 暂停等待人工审核
    - 这是「双中断机制」中的 LangGraph interrupt
      区别于外部 abort 抢占中断（任务级强制终止）
    """
    if not state.is_need_human_review:
        return {}

    # 收集本轮所有待播报文本
    all_text = " | ".join([t.text for t in state.audio_tasks])

    logger.info(f"[human_review] 触发人工审核, 内容长度={len(all_text)}")

    # 触发 LangGraph interrupt，暂停等待人工审核
    from langgraph.types import interrupt

    human_input = interrupt({
        "type": "content_review",
        "content": all_text,
        "thread_id": state.thread_id,
        "message": "请审核以下口播内容，回复 yes 通过或 no 拒绝",
    })

    approved = human_input.strip().lower() == "yes"
    logger.info(f"[human_review] 审核结果: {'通过' if approved else '拒绝'}")

    return {"human_review_result": approved}
