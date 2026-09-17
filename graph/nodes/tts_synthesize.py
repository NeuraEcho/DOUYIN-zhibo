"""
TTS 语音合成节点 - 按序串行合成，投递音频到外部队列
支持 MiniMax 和 火山引擎 双提供商
"""

import asyncio
import random
import re

from loguru import logger

from config.settings import settings
from graph.state import LiveState
from services.text_enricher_service import normalize_text_for_tts
from services.tts_factory import create_tts_adapter

# MiniMax Speech-2.8 情绪标签正则（用于 TTS 前剥离，防止非 MiniMax TTS 朗读标签文本）
_EMOTION_TAG_RE = re.compile(
    r'\((?:breath|chuckle|laughs|sighs|coughs|clear-throat|emm|groans|pant|'
    r'inhale|exhale|gasps|sniffs|snorts|burps|lip-smacking|humming|hissing|sneezes)\)',
    re.IGNORECASE,
)
_PAUSE_TAG_RE = re.compile(r'<#\d+(?:\.\d+)?#>')  # <#0.2#> 停顿标记


def _strip_emotion_tags(text: str) -> str:
    """剥离 MiniMax Speech-2.8 情绪标签和停顿标记，返回纯文本"""
    text = _EMOTION_TAG_RE.sub('', text)
    text = _PAUSE_TAG_RE.sub('', text)
    # 清理剥离后可能产生的多余空格
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


async def _enqueue_block_gap(sentence_index: int):
    """分片之间插入随机静音间隔（模拟真人换气节奏），仅 PCM 编码生效"""
    vc = settings.volcengine
    if vc.block_gap <= 0 or sentence_index == 0 or vc.encoding != "pcm":
        return
    gap = vc.block_gap + random.uniform(-0.1, 0.2)   # 轻微抖动，避免机械等间隔
    gap = max(0.15, min(gap, 0.6))
    # 按采样点数生成再乘 2（每个 16-bit 采样占 2 字节），保证字节数为偶数，
    # 否则 np.frombuffer(data, np.int16) 会因奇数字节报 "buffer size must be a multiple of element size"
    num_samples = int(vc.sample_rate * gap)
    silence = b"\x00" * (num_samples * 2)  # 16-bit mono PCM 静音
    from output.audio_queue import AudioQueueService
    await AudioQueueService.get_instance().enqueue(silence, sentence_index=sentence_index)


async def _enqueue_answer_trailing_gap(sentence_index: int, gap_seconds: float):
    """插入的回答音频与续读脚本之间加一段静音，避免回答直接流入脚本"""
    vc = settings.volcengine
    if gap_seconds <= 0 or vc.encoding != "pcm":
        return
    num_samples = int(vc.sample_rate * gap_seconds)
    silence = b"\x00" * (num_samples * 2)  # 偶数字节，避免 np.frombuffer 奇数字节报错
    from output.audio_queue import AudioQueueService
    await AudioQueueService.get_instance().enqueue(silence, sentence_index=sentence_index)


async def _pace_with_playback(target_index: int, prebuffer: int, session_manager, timeout: float = 600.0):
    """
    播放进度门控（逐句即时合成）：
    合成第 target_index 句前，等待 AudioPlayer 播放进度追近到 target_index - prebuffer，
    保证合成最多领先播放 prebuffer 句，使句尾插入点贴近实时。
    带超时兜底防死锁；检测到打断信号立即退出。
    """
    from output.audio_player import AudioPlayer
    player = AudioPlayer.get_instance()
    threshold = target_index - prebuffer
    interval = 0.02
    waited = 0.0
    while player.current_sentence_index < threshold:
        if session_manager.is_interrupt_requested():
            return
        if waited >= timeout:
            logger.warning(
                f"[speech_28_turbo] 播放进度门控超时（{timeout}s），"
                f"target={target_index}, player={player.current_sentence_index}，继续合成"
            )
            return
        await asyncio.sleep(interval)
        waited += interval


async def speech_28_turbo_call(state: LiveState) -> dict:
    """
    TTS 语音合成节点

    职责：
    - 取第一个未完成的音频任务
    - 文本预处理（数字转中文、清理 markdown）
    - 调用 TTS 接口合成语音（自动选择 MiniMax 或 火山引擎）
    - 投递音频分片到外部音频播放队列（异步，不阻塞）
    - 节点本身不执行播放

    策略：TTS 按序串行调用（句子级别），音频播放完全异步
    """
    # 取第一个未完成的音频任务
    pending = [t for t in state.audio_tasks if not t.is_complete]
    if not pending:
        return {}

    task = pending[0]

    # ===== 预就绪伺机插入：播放进度门控 + 句尾插入门控（仅脚本播报模式）=====
    from scheduler.session_manager import SessionManager
    session_manager = SessionManager.get_instance()
    qa_cfg = settings.qa_insertion
    if qa_cfg.enabled and session_manager.is_script_broadcast:
        # 1) 逐句即时合成：等待播放进度追近，最多领先 prebuffer 句
        await _pace_with_playback(task.sentence_index, qa_cfg.prebuffer_sentences, session_manager)
        if session_manager.is_interrupt_requested():
            return {"audio_tasks": state.audio_tasks}
        # 2) 句尾插入门控：若回答音频已就绪且在弹性窗口内，先把回答 enqueue 到本句之前
        from scheduler.qa_inserter import QAInserter
        answer_chunks = QAInserter.get_instance().try_consume_for_insertion(task.sentence_index)
        if answer_chunks:
            from output.audio_queue import AudioQueueService
            audio_service = AudioQueueService.get_instance()
            for chunk in answer_chunks:
                # sentence_index=task.sentence_index，因 chunk_index 全局单调递增，
                # 回答会排在上一句之后、本句脚本音频之前
                await audio_service.enqueue(chunk, sentence_index=task.sentence_index)
            await _enqueue_answer_trailing_gap(task.sentence_index, qa_cfg.answer_trailing_gap_seconds)

    adapter = create_tts_adapter()  # 通过工厂创建适配器

    # 决策4：直播链路音色由全局运行时配置决定 —— 应用试播/提问或配置页选定的克隆音色。
    # preview_router 触发前已 set_cloned_voice 写入运行时配置；此处读取并令适配器生效，
    # 否则直播合成始终用 .env 预置音色，前端所选音色对直播链路不生效。
    # 适用于火山引擎与 ElevenLabs（两者都是音色 ID 直接透传给厂商 API）；
    # MiniMax 的音色在 create_tts_adapter 时已从配置读取，不走这个运行时槽位。
    from web.routers.config_router import get_cloned_voice_id, get_tts_provider
    if get_tts_provider() in ("volcengine", "elevenlabs"):
        _cloned = get_cloned_voice_id()
        if _cloned:
            adapter.set_cloned_voice(_cloned)

    # 文本预处理：数字转中文、清理 markdown
    # 整体情绪由适配器在会话级通过 context_texts 全局下发，此处不再套逐块情绪标签
    tts_text = normalize_text_for_tts(task.text)
    # 安全过滤：剥离可能残留的 MiniMax 情绪标签（防止 TTS 朗读标签文本）
    tts_text = _strip_emotion_tags(tts_text)

    try:
        logger.info(
            f"[speech_28_turbo] 开始合成句子 #{task.sentence_index}, "
            f"文本长度={len(tts_text)}"
        )

        # 分片换气间隔（首块不加）
        await _enqueue_block_gap(task.sentence_index)

        audio_chunks = await adapter.synthesize(
            text=tts_text,
            cancel_check=session_manager.is_interrupt_requested,
        )

        # 合成期间被打断：跳过投递（避免淡出后又冒出残句），也不记录为已播报
        if session_manager.is_interrupt_requested():
            logger.warning(
                f"[speech_28_turbo] 句子 #{task.sentence_index} 合成中被打断，跳过投递"
            )
            return {"audio_tasks": state.audio_tasks}

        task.audio_chunks = audio_chunks
        task.is_complete = True

        # 投递音频片段到外部音频播放队列
        from output.audio_queue import AudioQueueService
        audio_service = AudioQueueService.get_instance()
        for chunk in audio_chunks:
            await audio_service.enqueue(chunk, sentence_index=task.sentence_index)

        # 记录已播报句子，供打断时捕获续接上下文
        session_manager.track_spoken(task.text)
        # 更新脚本断点索引（精确续接核心：记录当前播到第几句）
        session_manager.update_breakpoint(task.sentence_index)

        # 脚本收尾兜底：若这是最后一句且仍有就绪回答未插入，取出播放，
        # 避免临近脚本结尾的提问被丢弃（should_continue_tts 是同步路由无法 await，故在此处 flush）
        if qa_cfg.enabled and session_manager.is_script_broadcast:
            remaining_pending = [t for t in state.audio_tasks if not t.is_complete]
            if not remaining_pending:
                from scheduler.qa_inserter import QAInserter
                flush_chunks = QAInserter.get_instance().flush_if_ready()
                if flush_chunks:
                    for chunk in flush_chunks:
                        await audio_service.enqueue(chunk, sentence_index=task.sentence_index)
                    await _enqueue_answer_trailing_gap(
                        task.sentence_index, qa_cfg.answer_trailing_gap_seconds
                    )

        logger.info(
            f"[speech_28_turbo] 句子 #{task.sentence_index} 合成完成, "
            f"音频分片数={len(audio_chunks)}"
        )

        return {
            "audio_tasks": state.audio_tasks,
            "completed_audio_count": state.completed_audio_count + 1,
        }

    except asyncio.CancelledError:
        logger.warning("[speech_28_turbo] 任务被取消")
        return {"interrupt_reason": state.interrupt_reason}
    except Exception as e:
        logger.error(f"[speech_28_turbo] 异常: {e}")
        return {"error_message": f"TTS 合成失败: {e}"}
    finally:
        await adapter.close()
