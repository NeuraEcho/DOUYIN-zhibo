"""
配置管理路由 - 运行时配置热更新
"""

from typing import Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

# 运行时可变配置（可被后台动态覆盖）
_runtime_config = {
    "system_prompt": "",
    "tts_voice": "",
    "tts_speed": 1.0,
    "cloned_voice_id": "",  # 当前使用的克隆音色 ID
    "tts_provider": "minimax",  # tts 提供商: minimax / volcengine
    "context_text": None,  # 火山引擎整体语言指令（None=用 .env 默认；""=前端清空，不下发）
}


class PromptUpdateRequest(BaseModel):
    """更新主播人设 Prompt"""
    system_prompt: str


class TTSConfigUpdateRequest(BaseModel):
    """更新 TTS 参数"""
    voice: str = ""
    speed: float = 1.0


class TTSProviderUpdateRequest(BaseModel):
    """切换 TTS 提供商"""
    provider: str  # minimax / volcengine


class ContextTextUpdateRequest(BaseModel):
    """更新火山引擎整体语言指令（context_texts）"""
    context_text: str


class TimerConfigUpdateRequest(BaseModel):
    """更新定时播报配置"""
    interval_seconds: int = 180


@router.get("/prompt")
async def get_prompt():
    """获取当前主播人设 Prompt"""
    from config.settings import settings

    # 优先返回运行时覆盖值
    current = _runtime_config["system_prompt"] or settings.default_system_prompt
    return {"system_prompt": current}


@router.put("/prompt")
async def update_prompt(request: PromptUpdateRequest):
    """更新主播人设 Prompt（运行时热更新）"""
    _runtime_config["system_prompt"] = request.system_prompt
    logger.info(f"[config] 人设 Prompt 已热更新, 长度={len(request.system_prompt)}")
    return {"status": "ok", "message": "Prompt 已更新"}


@router.get("/tts")
async def get_tts_config():
    """获取当前 TTS 配置"""
    from config.settings import settings

    provider = _runtime_config.get("tts_provider", "minimax")
    result = {
        "provider": provider,
        "cloned_voice_id": _runtime_config.get("cloned_voice_id", ""),
    }

    if provider == "volcengine":
        vc = settings.volcengine
        result.update({
            "model": "seed-tts-2.0-websocket",
            "voice": vc.voice_type,
            "speed": vc.speed_ratio,
            "encoding": vc.encoding,
            "cluster": vc.cluster,
            "context_text": get_context_text(),
            "emotion_enabled": vc.emotion_enabled,
        })
    else:
        result.update({
            "model": settings.speech.model,
            "voice": _runtime_config["tts_voice"] or settings.speech.voice,
            "speed": _runtime_config["tts_speed"] or settings.speech.speed,
        })

    return result


@router.put("/tts")
async def update_tts_config(request: TTSConfigUpdateRequest):
    """更新 TTS 参数（运行时热更新）"""
    if request.voice:
        _runtime_config["tts_voice"] = request.voice
    _runtime_config["tts_speed"] = request.speed
    logger.info(f"[config] TTS 配置已热更新: voice={request.voice}, speed={request.speed}")
    return {"status": "ok", "message": "TTS 配置已更新"}


@router.put("/timer")
async def update_timer_config(request: TimerConfigUpdateRequest):
    """更新循环口播间隔（整稿念完后，停顿多少秒再从头开始下一遍）"""
    from scheduler.session_manager import SessionManager

    SessionManager.get_instance().set_loop_gap(request.interval_seconds)
    return {"status": "ok", "message": f"循环口播间隔已更新为 {request.interval_seconds} 秒"}


def set_cloned_voice(voice_id: str):
    """设置当前使用的克隆音色 ID（供 voice_clone_router 调用）"""
    _runtime_config["cloned_voice_id"] = voice_id
    logger.info(f"[config] 克隆音色已切换: voice_id={voice_id}")


def get_cloned_voice_id() -> str:
    """获取当前克隆音色 ID"""
    return _runtime_config.get("cloned_voice_id", "")


def get_prompt() -> str:
    """获取当前系统 Prompt（优先返回运行时覆盖值）"""
    return _runtime_config.get("system_prompt", "")


def get_tts_provider() -> str:
    """获取当前 TTS 提供商"""
    return _runtime_config.get("tts_provider", "minimax")


def get_context_text() -> str:
    """获取火山引擎整体语言指令（前端运行时覆盖优先，否则用 .env 默认）"""
    from config.settings import settings
    rt = _runtime_config.get("context_text")
    if rt is None:                       # 从未在前端设置过 → 用 .env 默认
        return settings.volcengine.context_text.strip()
    return rt.strip()                    # 前端设置过（含空串）→ 以前端为准


def set_context_text(text: str):
    """设置火山引擎整体语言指令（运行时热更新，空串表示不下发情绪指令）"""
    _runtime_config["context_text"] = (text or "").strip()
    logger.info(f"[config] 整体语言指令已更新: 长度={len(_runtime_config['context_text'])}")


def set_tts_provider(provider: str):
    """设置当前 TTS 提供商"""
    _runtime_config["tts_provider"] = provider
    logger.info(f"[config] TTS 提供商已切换: {provider}")


@router.put("/tts-provider")
async def update_tts_provider(request: TTSProviderUpdateRequest):
    """切换 TTS 提供商（运行时热更新）"""
    if request.provider not in ("minimax", "volcengine"):
        return {"status": "error", "message": "不支持的 TTS 提供商，可选: minimax / volcengine"}
    set_tts_provider(request.provider)
    return {"status": "ok", "message": f"TTS 提供商已切换为: {request.provider}"}


@router.put("/context-text")
async def update_context_text(request: ContextTextUpdateRequest):
    """更新火山引擎整体语言指令（context_texts，合成时全局生效，预置+复刻音色均适用）"""
    set_context_text(request.context_text)
    return {"status": "ok", "message": "整体语言指令已更新", "context_text": get_context_text()}


class DanmakuFilterUpdateRequest(BaseModel):
    """更新弹幕过滤规则（只传需要改的项，未传项保持不变）"""
    keywords: Optional[list[str]] = None       # 打断关键词列表
    cooldown_seconds: Optional[float] = None   # 冷却时间（秒）
    min_text_length: Optional[int] = None      # 最小弹幕字数


class FallbackNamesUpdateRequest(BaseModel):
    """整体覆盖点名兜底名称池"""
    names: list[str] = []


@router.get("/danmaku-filter")
async def get_danmaku_filter():
    """获取当前弹幕过滤规则（关键词/冷却/最小字数）"""
    from scheduler.danmaku_filter import DanmakuFilter
    return DanmakuFilter.get_instance().get_config()


@router.put("/danmaku-filter")
async def update_danmaku_filter(request: DanmakuFilterUpdateRequest):
    """更新弹幕过滤规则（运行时热更新，重启恢复 .env 默认）"""
    from scheduler.danmaku_filter import DanmakuFilter
    DanmakuFilter.get_instance().update_config(
        keywords=request.keywords,
        cooldown_seconds=request.cooldown_seconds,
        min_text_length=request.min_text_length,
    )
    return {
        "status": "ok",
        "message": "弹幕过滤规则已更新",
        "config": DanmakuFilter.get_instance().get_config(),
    }


@router.get("/fallback-names")
async def get_fallback_names():
    """获取点名兜底名称池（真实互动池为空时随机点名取用）"""
    from scheduler.viewer_pool import ViewerPool
    return {"names": ViewerPool.get_instance().get_fallback_names()}


@router.put("/fallback-names")
async def update_fallback_names(request: FallbackNamesUpdateRequest):
    """整体覆盖点名兜底名称池（运行时热更新）"""
    from scheduler.viewer_pool import ViewerPool
    ViewerPool.get_instance().set_fallback_names(request.names)
    return {
        "status": "ok",
        "message": "兜底名称池已更新",
        "names": ViewerPool.get_instance().get_fallback_names(),
    }


class ProductExampleItem(BaseModel):
    """单条问答示例"""
    question: str
    answer: str


class ProductUpdateRequest(BaseModel):
    """更新商品问答资料（仅用于回答观众提问）"""
    product_info: str = ""
    examples: list[ProductExampleItem] = []


@router.get("/product")
async def get_product():
    """获取商品问答资料（商品信息 + 问答示例）"""
    from scheduler.product_knowledge import ProductKnowledge
    return ProductKnowledge.get_instance().get_data()


@router.put("/product")
async def update_product(request: ProductUpdateRequest):
    """更新商品问答资料（持久化到 data/product_knowledge.json，重启不丢）"""
    from scheduler.product_knowledge import ProductKnowledge
    ProductKnowledge.get_instance().update(
        product_info=request.product_info,
        examples=[ex.model_dump() for ex in request.examples],
    )
    return {
        "status": "ok",
        "message": "商品问答资料已保存",
        "data": ProductKnowledge.get_instance().get_data(),
    }
