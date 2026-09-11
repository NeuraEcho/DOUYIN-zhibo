"""
Prompt 组装节点 - 拼接系统人设 + 滑动窗口对话上下文 + 打断上下文衔接
播报模式和问答模式使用完全独立的提示词体系，避免 LLM 混淆行为。
"""

from loguru import logger

from graph.state import InterruptReason, LiveState, RunMode

# ===== 问答模式独立系统提示词（与播报人设完全分离，避免 LLM 混淆）=====
# 导出为模块级常量，供 scheduler/qa_inserter.py 预生成回答时复用
QA_SYSTEM_PROMPT = (
    "你是直播间的AI主播助理。现在观众提了一个问题，你需要用一两句话简洁回答。\n\n"
    "要求：\n"
    "- 直接回答，不要加\u201c欢迎\u201d、\u201c打个一\u201d等播报话术\n"
    "- 不要重复之前说过的内容\n"
    "- 语气自然口语化，像主播随口回应观众\n"
    "- 回答完就停，不要续接其他内容\n"
    "- 不要推销产品或引导下单"
)


def prompt_assembler(state: LiveState) -> dict:
    """
    Prompt 组装节点

    职责：
    - 根据运行模式选择独立的系统提示词（播报 vs 问答完全分离）
    - 构建滑动窗口对话上下文
    - 打断上下文衔接：被中断的内容作为上下文注入
    - 根据运行模式组装用户消息
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    messages = []

    # 1. 系统提示词：根据模式选择完全独立的提示词体系
    if state.run_mode == RunMode.DANMAKU_REPLY:
        # 问答模式：使用独立问答提示词，不含播报人设
        messages.append(SystemMessage(content=QA_SYSTEM_PROMPT))
        # 商品问答知识库：注入商品信息 + 参考示例（仅问答链路，播报不受影响）
        from scheduler.product_knowledge import ProductKnowledge
        product_ctx = ProductKnowledge.get_instance().build_qa_context()
        if product_ctx:
            messages.append(SystemMessage(content=product_ctx))
    else:
        # 播报模式（SCRIPT_AUTO / MANUAL_INPUT / IDLE）：使用主播播报人设
        messages.append(SystemMessage(content=state.system_prompt))

    # 2. 打断上下文衔接（仅在上一轮被抢占打断且有已播报内容时激活）
    if state.interrupt_reason != InterruptReason.NONE and state.interrupted_text:
        if state.run_mode == RunMode.DANMAKU_REPLY:
            # 弹幕回复：仅告知 LLM 刚才被打断了，简短承接后直接回答
            context_hint = (
                f"[系统提示] 你刚才正在播报，但说到「{state.interrupted_text}」时被观众提问打断了。\n"
                f"请先用一句自然、口语化的话轻轻承接一下（例如\u201c稍等哈，先回答这位朋友的问题\u201d），\n"
                f"然后立即简洁回答当前问题。不要续读或重复之前没说完的内容。"
            )
            messages.append(SystemMessage(content=context_hint))
        # 播报模式不需要打断上下文（script_resume 走 bypass_llm，不经过 LLM）

    # 3. 滑动窗口对话上下文（取最近 N 轮）
    window = state.messages[-(state.context_window_size * 2):]
    messages.extend(window)

    # 4. 当前触发内容
    trigger = state.trigger_content
    if state.run_mode == RunMode.SCRIPT_AUTO:
        messages.append(HumanMessage(
            content=f"[定时播报任务] 请根据以下脚本内容，用你的主播风格自然地播报：\n{trigger}"
        ))
    elif state.run_mode == RunMode.DANMAKU_REPLY:
        messages.append(HumanMessage(content=trigger))
        # 带昵称回答：注入提问观众的格式化称呼，让 AI 回答时先自然地叫一声「xx姐姐」
        # （系统消息放在 HumanMessage 之后，靠近生成点，约束更强）
        from common.nickname_formatter import format_viewer_nickname
        call_name = format_viewer_nickname(state.trigger_nickname)
        messages.append(SystemMessage(content=(
            f"这位提问观众的称呼是「{call_name}」。请在回答开头自然地叫一声「{call_name}」，"
            f"然后简洁回答问题本身；只称呼一次，不要加欢迎/引导打字等播报话术。"
        )))
    elif state.run_mode == RunMode.MANUAL_INPUT:
        messages.append(HumanMessage(content=f"[运营手动下发] {trigger}"))

    logger.info(
        f"[prompt_assembler] 消息数={len(messages)}, "
        f"上下文窗口={state.context_window_size}, "
        f"运行模式={state.run_mode.value}"
    )

    return {"messages": messages}
