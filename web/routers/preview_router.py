"""
试听试播路由 - 话术试听、试播模式、模拟弹幕提问
不依赖 LangGraph 编排，直接调用 LLM/TTS 适配器进行独立测试
"""

import base64
import struct
from typing import Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from config.settings import settings
from scheduler.event_bus import EventBus, EventType, LiveEvent
from services.text_enricher_service import TextEnricherService
from services.tts_factory import create_tts_adapter

router = APIRouter()


# ---------- 请求模型 ----------

class TTSPreviewRequest(BaseModel):
    """话术试听请求"""
    text: str
    voice_id: Optional[str] = None  # 指定克隆音色 ID，空则用默认
    enrich: bool = True  # 是否开启口语化标注


class TestBroadcastRequest(BaseModel):
    """试播请求"""
    script: str  # 用户输入的脚本文本
    voice_id: Optional[str] = None
    enrich: bool = True  # 是否开启口语化标注


class SimulateDanmakuRequest(BaseModel):
    """模拟弹幕提问请求"""
    question: str  # 模拟的观众提问
    nickname: Optional[str] = None  # 自定义观众昵称（空则回答时兜底称呼「用户姐姐」）
    voice_id: Optional[str] = None
    do_tts: bool = True  # 是否同时合成语音
    enrich: bool = True  # 是否开启口语化标注


# ---------- 工具函数 ----------

def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """将 PCM 原始音频数据封装为 WAV 格式"""
    data_size = len(pcm_data)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16,                  # fmt chunk size
        1,                   # PCM format
        channels,
        sample_rate,
        sample_rate * channels * bits_per_sample // 8,  # byte rate
        channels * bits_per_sample // 8,                 # block align
        bits_per_sample,
        b'data',
        data_size,
    )
    return header + pcm_data


async def _call_tts(text: str, voice_id: Optional[str] = None) -> bytes:
    """
    调用 TTS 合成音频，返回 WAV 格式音频数据

    :param text: 待合成文本
    :param voice_id: 指定音色 ID（系统音色或克隆音色）
    :return: WAV 格式音频 bytes
    """
    from data.system_voices import CHINESE_VOICES, CANTONESE_VOICES, ENGLISH_VOICES
    from services.text_enricher_service import normalize_text_for_tts
    from web.routers.config_router import get_tts_provider

    # 文本预处理：数字转中文、清理 markdown
    text = normalize_text_for_tts(text)
    logger.info(f"[TTS] 文本预处理后: '{text[:50]}...'")

    # 通过工厂创建适配器（根据当前 TTS 提供商）
    adapter = create_tts_adapter()

    # 构建系统音色 voice_id 集合
    system_voice_ids = set()
    for v in CHINESE_VOICES + CANTONESE_VOICES + ENGLISH_VOICES:
        system_voice_ids.add(v["voice_id"])

    if voice_id:
        if get_tts_provider() == "minimax":
            if voice_id in system_voice_ids:
                # 系统音色：直接设置为默认音色，不走克隆逻辑
                adapter._default_voice = voice_id
                logger.info(f"[TTS] 使用系统音色: {voice_id}")
            else:
                # 克隆音色
                adapter.set_cloned_voice(voice_id)
                logger.info(f"[TTS] 使用克隆音色: {voice_id}")
        else:
            # 火山引擎：所有 voice_id 都作为 voice_type
            adapter.set_cloned_voice(voice_id)
            logger.info(f"[TTS] 使用音色: {voice_id}")
    else:
        # 检查全局运行时是否设置了克隆音色
        from web.routers.config_router import get_cloned_voice_id
        cloned = get_cloned_voice_id()
        if cloned:
            adapter.set_cloned_voice(cloned)

    try:
        pcm_chunks = await adapter.synthesize(text)
        pcm_data = b"".join(pcm_chunks)
        # MiniMax 和 火山引擎 V3 都输出 PCM 裸音频，统一封装 WAV
        provider = get_tts_provider()
        if provider == "volcengine":
            sample_rate = settings.volcengine.sample_rate
        else:
            sample_rate = settings.speech.sample_rate
        wav_data = _pcm_to_wav(pcm_data, sample_rate=sample_rate)
        return wav_data
    finally:
        await adapter.close()


# ---------- API 接口 ----------

@router.post("/tts-preview")
async def tts_preview(request: TTSPreviewRequest):
    """
    话术试听

    输入文本和可选音色，调用 TTS 合成语音，返回 WAV 音频供浏览器播放。
    不经过 LangGraph 编排，直接调用 TTS 适配器。
    """
    if not request.text.strip():
        return {"status": "error", "message": "文本不能为空"}

    logger.info(f"[preview] 话术试听: text长度={len(request.text)}, voice={request.voice_id or '默认'}, enrich={request.enrich}")

    try:
        from web.routers.config_router import get_tts_provider
        provider = get_tts_provider()
        tts_text = request.text

        if provider == "volcengine":
            # 火山引擎：直接使用原稿合成，不做 Agent 改写；整体语气由适配器 context_texts 全局控制（前端可填）
            wav_data = await _call_tts(request.text, request.voice_id)
        else:
            # MiniMax：口语化标注
            if request.enrich:
                enricher = TextEnricherService.get_instance()
                tts_text = await enricher.enrich_text(request.text)
                logger.info(f"[preview] 口语化标注: '{request.text[:30]}...' → '{tts_text[:50]}...'")
            wav_data = await _call_tts(tts_text, request.voice_id)

        # 返回 base64 编码的音频 + 文本信息
        audio_b64 = base64.b64encode(wav_data).decode("utf-8")
        return {
            "status": "ok",
            "audio_base64": audio_b64,
            "audio_size": len(wav_data),
            "text_length": len(request.text),
            "enriched_text": (tts_text if request.enrich else None) if provider != "volcengine" else None,
            "provider": provider,
        }
    except Exception as e:
        logger.error(f"[preview] TTS 试听失败: {e}")
        return {"status": "error", "message": f"TTS 合成失败: {e}"}


@router.post("/test-broadcast")
async def test_broadcast(request: TestBroadcastRequest):
    """
    试播模式（并入真实直播会话）

    触发一次「直读播报」直播会话：脚本原样进入 LangGraph（bypass LLM）
    → 分句 → TTS → 音频队列 → 虚拟声卡输出。可被后续「模拟提问」打断并续接。
    浏览器不再返回音频，声音从虚拟声卡输出（需安装 VB-Audio Virtual Cable）。
    """
    if not request.script.strip():
        return {"status": "error", "message": "脚本不能为空"}

    logger.info(f"[preview] 试播（直播会话·稿子直读）: script长度={len(request.script)}, voice={request.voice_id or '默认'}")

    try:
        # 若指定音色，同步到全局运行时配置（直播链路音色由全局配置决定）
        if request.voice_id:
            from web.routers.config_router import set_cloned_voice
            set_cloned_voice(request.voice_id)

        # 触发直播会话（source=preview_broadcast → event_router 置 bypass_llm=True）
        await EventBus.get_instance().publish(LiveEvent(
            event_type=EventType.AUTO_BROADCAST,
            source="preview_broadcast",
            content=request.script,
        ))
        return {
            "status": "ok",
            "message": "已开始试播（声音从虚拟声卡输出，浏览器不再播放）",
            "script": request.script,
            "text_length": len(request.script),
        }
    except Exception as e:
        logger.error(f"[preview] 试播触发失败: {e}")
        return {"status": "error", "message": f"试播失败: {e}"}


@router.post("/simulate-danmaku")
async def simulate_danmaku(request: SimulateDanmakuRequest):
    """
    模拟弹幕提问（并入真实直播会话）

    触发弹幕回复会话：走 LLM 生成回复 → 分句 → TTS → 虚拟声卡输出。
    若此时试播正在播报，session_manager.start_session 会检测到 busy 并自动
    abort(DANMAKU_INTERRUPT) 打断、捕获续接，新一轮由 prompt_assembler 注入
    口语化承接提示后再回答，实现「打断 → 承接 → 回答」。
    浏览器不再返回音频，声音从虚拟声卡输出。
    """
    if not request.question.strip():
        return {"status": "error", "message": "提问不能为空"}

    logger.info(f"[preview] 模拟弹幕提问（直播会话）: question={request.question[:50]}..., nickname={request.nickname or '用户'}, voice={request.voice_id or '默认'}")

    try:
        # 若指定音色，同步到全局运行时配置
        if request.voice_id:
            from web.routers.config_router import set_cloned_voice
            set_cloned_voice(request.voice_id)

        # 触发弹幕会话（source=preview_danmaku → DANMAKU_REPLY，走 LLM 回答）
        # 携带自定义昵称：回答时会格式化为「xx姐姐」称呼（空则兜底「用户姐姐」）
        await EventBus.get_instance().publish(LiveEvent(
            event_type=EventType.DANMAKU_MESSAGE,
            source="preview_danmaku",
            content=request.question,
            metadata={"user_name": request.nickname or ""},
        ))
        return {
            "status": "ok",
            "message": "已发送提问（若正在播报将自动打断并由 AI 承接口播后回答）",
            "question": request.question,
        }
    except Exception as e:
        logger.error(f"[preview] 模拟弹幕触发失败: {e}")
        return {"status": "error", "message": f"模拟失败: {e}"}
