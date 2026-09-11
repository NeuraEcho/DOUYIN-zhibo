"""
流式管线 LangGraph 节点
将 StreamingPipeline 服务包装为 LangGraph 节点函数
"""

from loguru import logger

from graph.state import LiveState

# LangChain message type → OpenAI API role 映射
_ROLE_MAP = {
    "system": "system",
    "human": "user",
    "ai": "assistant",
    "user": "user",
    "assistant": "assistant",
}


async def streaming_pipeline_node(state: LiveState) -> dict:
    """
    流式管线 LangGraph 节点

    职责：
    - 将 LangChain messages 转换为 API 格式
    - 调用 StreamingPipeline 执行 LLM→分句→TTS 并发管线
    - 返回完整的状态更新（audio_tasks、raw_llm_output 等）

    效果：
    - 弹幕回复场景首音延迟从 3-8s 压缩到 200-600ms
    - LLM 生成第 2 句时 TTS 已在合成第 1 句
    """
    from services.streaming_pipeline import StreamingPipeline

    # 将 LangChain messages 转换为 API 格式
    api_messages = []
    for msg in state.messages:
        msg_type = getattr(msg, "type", "unknown")
        role = _ROLE_MAP.get(msg_type)
        if role is None:
            logger.warning(f"[streaming_pipeline_node] 未知消息类型: {msg_type}，跳过")
            continue
        api_messages.append({"role": role, "content": msg.content})

    from scheduler.session_manager import SessionManager
    session_manager = SessionManager.get_instance()

    try:
        logger.info(
            f"[streaming_pipeline_node] 启动流式管线, "
            f"消息数={len(api_messages)}, "
            f"运行模式={state.run_mode.value}"
        )

        pipeline = StreamingPipeline.get_instance()
        result = await pipeline.run(
            messages=api_messages,
            system_prompt=state.system_prompt,
            cancel_check=session_manager.is_interrupt_requested,
        )

        logger.info(
            f"[streaming_pipeline_node] 流式管线完成, "
            f"句子数={len(result.get('audio_tasks', []))}, "
            f"全文长度={len(result.get('raw_llm_output', ''))}"
        )

        return result

    except Exception as e:
        logger.error(f"[streaming_pipeline_node] 流式管线异常: {e}")
        return {
            "error_message": f"流式管线失败: {e}",
            "streaming_processed": True,
        }
