"""
DeepSeek-V4-Flash 流式调用节点 - 调用云端 SSE 流式接口
"""

import asyncio

from loguru import logger

from adapters.deepseek_adapter import DeepSeekR1Adapter
from graph.state import LiveState

# LangChain message type → OpenAI API role 映射
_ROLE_MAP = {
    "system": "system",
    "human": "user",
    "ai": "assistant",
    "user": "user",
    "assistant": "assistant",
}


async def deepseek_r1_stream(state: LiveState) -> dict:
    """
    DeepSeek-V4-Flash 流式调用节点

    职责：
    - 调用云端 SSE 流式接口
    - 逐 token 接收返回文本
    - 支持主动取消（中断长连接）
    - 异常捕获与错误上报
    - 直读模式（bypass_llm）：稿子原样返回，旁路 LLM
    """
    # 直读模式（试播/循环口播）：稿子原样播报，旁路 LLM，不做任何改写
    if state.bypass_llm:
        from scheduler.session_manager import SessionManager
        from scheduler.viewer_pool import ViewerPool
        # 循环口播模式：每遍只读一次（循环由 _schedule_loop_session 驱动）；
        # 试播模式：读 10 遍，留足时间供打断测试
        loop_count = 1 if SessionManager.get_instance().is_loop_broadcast else 10
        # 随机点名：把稿子里的 {点名} 占位符替换为最近互动观众的格式化昵称
        base_text = ViewerPool.get_instance().fill_mentions(state.trigger_content)
        looped_text = base_text * loop_count
        logger.info(
            f"[deepseek_r1_stream] 直读模式：跳过 LLM，稿子循环播报 {loop_count} 遍, "
            f"单遍长度={len(base_text)}, 总长度={len(looped_text)}"
        )
        return {"raw_llm_output": looped_text}

    from scheduler.session_manager import SessionManager
    session_manager = SessionManager.get_instance()

    adapter = DeepSeekR1Adapter()

    # 将 LangChain messages 转换为 API 格式
    api_messages = []
    for msg in state.messages:
        msg_type = getattr(msg, "type", "unknown")
        role = _ROLE_MAP.get(msg_type)
        if role is None:
            logger.warning(f"[deepseek_r1_stream] 未知消息类型: {msg_type}，跳过")
            continue
        api_messages.append({"role": role, "content": msg.content})

    try:
        logger.info("[deepseek_r1_stream] 开始调用 DeepSeek-R1")

        full_text = await adapter.stream_chat(
            messages=api_messages,
            cancel_check=session_manager.is_interrupt_requested,
        )

        logger.info(f"[deepseek_r1_stream] 完成, 输出长度={len(full_text)}")
        return {"raw_llm_output": full_text}

    except asyncio.CancelledError:
        logger.warning("[deepseek_r1_stream] 任务被取消")
        return {
            "raw_llm_output": state.raw_llm_output,
            "interrupt_reason": state.interrupt_reason,
        }
    except Exception as e:
        logger.error(f"[deepseek_r1_stream] 异常: {e}")
        return {"error_message": f"DeepSeek-R1 调用失败: {e}"}
    finally:
        await adapter.close()
