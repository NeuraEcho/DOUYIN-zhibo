"""
文本口语化标注节点 - 在 TTS 之前用 LLM 将书面文案转为口语化话术
"""

from loguru import logger

from graph.state import LiveState, RunMode
from services.text_enricher_service import TextEnricherService


async def text_enricher(state: LiveState) -> dict:
    """
    文本口语化标注节点

    - MiniMax：LLM 口语化改写 + 语气词标签
    - 火山引擎：直接使用原始稿件，不做任何 Agent 改写；
                整体语气/情绪由适配器在会话级通过 context_texts（前端可填）全局控制
    - 弹幕回复：跳过标注，减少延迟，避免情绪标签混入快速问答
    """
    from web.routers.config_router import get_tts_provider
    provider = get_tts_provider()

    # ===== 火山引擎：直接用稿子，不经过 Agent 口语化 =====
    if provider == "volcengine":
        logger.info("[text_enricher] 火山引擎：直接使用原始稿件合成，跳过 Agent 口语化")
        return {}

    # ===== 弹幕回复：跳过标注，减少延迟，避免 MiniMax 情绪标签混入 =====
    if state.run_mode == RunMode.DANMAKU_REPLY:
        logger.info("[text_enricher] 弹幕回复模式：跳过口语化标注，减少延迟")
        return {}

    # 获取待合成的任务
    pending = [t for t in state.audio_tasks if not t.is_complete]
    if not pending:
        return {}

    enricher = TextEnricherService.get_instance()

    # 如果未启用，直接跳过
    if not enricher.is_enabled():
        logger.info("[text_enricher] 口语化标注未启用，跳过")
        return {}

    # ===== MiniMax：原有口语化标注 =====
    original_texts = [t.text for t in pending]
    logger.info(
        f"[text_enricher] 开始口语化标注, "
        f"句子数={len(original_texts)}, "
        f"总字数={sum(len(t) for t in original_texts)}"
    )

    # 批量标注
    enriched_texts = await enricher.enrich_sentences(original_texts)

    # 更新 audio_tasks 中的文本
    updated_tasks = list(state.audio_tasks)
    pending_idx = 0
    for i, task in enumerate(updated_tasks):
        if not task.is_complete:
            if pending_idx < len(enriched_texts):
                task.text = enriched_texts[pending_idx]
                logger.debug(
                    f"[text_enricher] 句子 #{task.sentence_index}: "
                    f"'{original_texts[pending_idx][:30]}...' → "
                    f"'{enriched_texts[pending_idx][:50]}...'"
                )
                pending_idx += 1

    logger.info(
        f"[text_enricher] 口语化标注完成, "
        f"已更新 {pending_idx} 个句子的文本"
    )

    return {"audio_tasks": updated_tasks}
