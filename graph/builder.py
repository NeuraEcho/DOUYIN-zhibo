"""
LangGraph StateGraph 构建器
组装所有节点和边，构建完整的直播主播 Graph
"""

from langgraph.graph import END, START, StateGraph
from loguru import logger

from graph.nodes.error_handler import error_handler
from graph.nodes.event_router import event_router
from graph.nodes.human_review import human_review_gate
from graph.nodes.llm_stream import deepseek_r1_stream
from graph.nodes.prompt_assembler import prompt_assembler
from graph.nodes.round_complete import round_complete
from graph.nodes.sentence_splitter import sentence_splitter
from graph.nodes.streaming_pipeline import streaming_pipeline_node
from graph.nodes.text_enricher import text_enricher
from graph.nodes.tts_synthesize import speech_28_turbo_call
from graph.state import LiveState, RunMode


def should_continue_tts(state: LiveState) -> str:
    """
    TTS 循环判断路由
    判断是否还有待合成的句子
    """
    # 协作式打断：检测到打断信号立即退出 TTS 循环，
    # 交给 round_complete 把已播报内容写入记忆并清理状态
    from scheduler.session_manager import SessionManager
    if SessionManager.get_instance().is_interrupt_requested():
        logger.warning("[should_continue_tts] 检测到打断信号，退出 TTS 循环 → round_complete")
        return "round_complete"

    if state.error_message:
        return "error_handler"

    # 人工审核拒绝
    if state.is_need_human_review and state.human_review_result is False:
        logger.info("[should_continue_tts] 人工审核拒绝，跳过 TTS")
        return "round_complete"

    pending = [t for t in state.audio_tasks if not t.is_complete]
    if pending:
        return "tts_synthesize"
    else:
        return "round_complete"


def route_after_prompt_assembler(state: LiveState) -> str:
    """
    Prompt 组装后的条件路由：
    - DANMAKU_REPLY → 流式管线（LLM→分句→TTS 并发，首音延迟最低）
    - 其他模式 → 串行链路（LLM→分句→标注→TTS 循环）
    """
    if state.run_mode == RunMode.DANMAKU_REPLY:
        logger.info("[route_after_prompt_assembler] 弹幕回复 → 流式管线快车道")
        return "streaming_pipeline"
    else:
        logger.info(f"[route_after_prompt_assembler] 模式 {state.run_mode.value} → 串行链路")
        return "llm_stream"


def build_live_graph() -> StateGraph:
    """
    构建直播主播 LangGraph StateGraph

    节点清单：
    1. event_router          - 事件路由
    2. prompt_assembler      - Prompt 组装
    3. streaming_pipeline    - 流式管线（LLM→分句→TTS 并发，弹幕回复快车道）
    4. deepseek_r1_stream    - LLM 流式调用（串行链路）
    5. sentence_splitter     - 分句切割
    6. text_enricher         - 文本口语化标注
    7. human_review_gate     - 人工审核（可选）
    8. speech_28_turbo_call  - TTS 语音合成
    9. round_complete        - 结束与状态重置
    10. error_handler        - 异常处理
    """
    graph = StateGraph(LiveState)

    # 注册节点
    graph.add_node("event_router", event_router)
    graph.add_node("prompt_assembler", prompt_assembler)
    graph.add_node("streaming_pipeline", streaming_pipeline_node)
    graph.add_node("deepseek_r1_stream", deepseek_r1_stream)
    graph.add_node("sentence_splitter", sentence_splitter)
    graph.add_node("text_enricher", text_enricher)
    graph.add_node("human_review_gate", human_review_gate)
    graph.add_node("speech_28_turbo_call", speech_28_turbo_call)
    graph.add_node("round_complete", round_complete)
    graph.add_node("error_handler", error_handler)

    # ===== 边连接 =====

    # 入口 → 事件路由 → Prompt 组装
    graph.add_edge(START, "event_router")
    graph.add_edge("event_router", "prompt_assembler")

    # Prompt 组装后条件路由：弹幕回复走流式快车道，其他走串行链路
    graph.add_conditional_edges(
        "prompt_assembler",
        route_after_prompt_assembler,
        {
            "streaming_pipeline": "streaming_pipeline",
            "llm_stream": "deepseek_r1_stream",
        },
    )

    # 流式管线完成 → 直接结束（TTS 已在管线内全部完成）
    graph.add_edge("streaming_pipeline", "round_complete")

    # 串行链路：LLM → 分句 → 标注 → 人工审核
    graph.add_edge("deepseek_r1_stream", "sentence_splitter")
    graph.add_edge("sentence_splitter", "text_enricher")
    graph.add_edge("text_enricher", "human_review_gate")

    # TTS 循环：条件边判断是否还有待合成句子
    graph.add_conditional_edges(
        "human_review_gate",
        should_continue_tts,
        {
            "tts_synthesize": "speech_28_turbo_call",
            "round_complete": "round_complete",
            "error_handler": "error_handler",
        },
    )

    # TTS 完成后回到判断节点（循环）
    graph.add_edge("speech_28_turbo_call", "human_review_gate")

    # 结束
    graph.add_edge("round_complete", END)
    graph.add_edge("error_handler", END)

    logger.info("[build_live_graph] LangGraph StateGraph 构建完成（含流式管线快车道）")
    return graph
