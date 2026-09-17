"""
事件路由节点 - 判断触发来源，初始化本轮运行模式
"""

from loguru import logger

from graph.state import LiveState, RunMode


def event_router(state: LiveState) -> dict:
    """
    事件路由节点

    职责：
    - 判断触发来源（定时脚本 / 弹幕提问 / 人工话术）
    - 初始化本轮运行模式
    - 重置本轮临时状态
    """
    source = state.trigger_source
    bypass = False

    if source == "timer":
        mode = RunMode.SCRIPT_AUTO
    elif source in ("danmaku", "channels"):
        # danmaku=抖音弹幕；channels=视频号弹幕（wxlivespy 转发）→ 同走问答链路
        mode = RunMode.DANMAKU_REPLY
    elif source == "manual":
        mode = RunMode.MANUAL_INPUT
    elif source == "preview_broadcast":
        # 试播：稿子直读（旁路 LLM），脚本原样进入分句 → TTS → 声卡
        mode = RunMode.SCRIPT_AUTO
        bypass = True
    elif source == "preview_danmaku":
        # 模拟提问：走 LLM 回答（可打断正在进行的试播并续接）
        mode = RunMode.DANMAKU_REPLY
    elif source == "script_resume":
        # 脚本续接：弹幕回答完成后，从断点继续播报剩余脚本（稿子直读，旁路 LLM）
        mode = RunMode.SCRIPT_AUTO
        bypass = True
    else:
        mode = RunMode.IDLE

    logger.info(f"[event_router] 触发来源={source}, 运行模式={mode.value}, 直读={bypass}")

    return {
        "run_mode": mode,
        "bypass_llm": bypass,
        "is_round_complete": False,
        "raw_llm_output": "",
        "text_segment_buffer": "",
        "current_text_chunk": "",
        "sentence_counter": 0,
        "audio_tasks": [],
        "completed_audio_count": 0,
        "error_message": None,
    }
