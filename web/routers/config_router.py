"""
配置管理路由 - 运行时配置热更新
"""

from typing import Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

# 支持的 TTS 提供商（新增厂商只需改这里 + tts_factory）
_SUPPORTED_TTS_PROVIDERS = ("minimax", "volcengine", "elevenlabs")

# 运行时可变配置（可被后台动态覆盖）
_runtime_config = {
    "system_prompt": "",
    "tts_voice": "",
    "tts_speed": 1.0,
    "cloned_voice_id": "",  # 当前使用的克隆音色 ID
    "tts_provider": "minimax",  # tts 提供商: minimax / volcengine / elevenlabs
    "context_text": None,  # 火山引擎整体语言指令（None=用 .env 默认；""=前端清空，不下发）
    "elevenlabs_model": None,  # ElevenLabs model_id（None=用 .env 默认）
    "room_id": "",  # 直播间号 ROOM_ID（空=用 .env 默认；前端修改后热更新并写回 .env）
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
    provider: str  # minimax / volcengine / elevenlabs


class ElevenLabsConfigUpdateRequest(BaseModel):
    """更新 ElevenLabs 运行时配置（只传需要改的项，未传项保持不变）"""
    model_id: Optional[str] = None


class ContextTextUpdateRequest(BaseModel):
    """更新火山引擎整体语言指令（context_texts）"""
    context_text: str


class TimerConfigUpdateRequest(BaseModel):
    """更新定时播报配置"""
    interval_seconds: int = 180


class RoomIdUpdateRequest(BaseModel):
    """更新直播间号 ROOM_ID"""
    room_id: str
    reconnect: bool = True  # 是否立即让弹幕采集器用新房间号重连


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
    elif provider == "elevenlabs":
        el = settings.elevenlabs
        result.update({
            "model": get_elevenlabs_model(),
            "voice": _runtime_config.get("cloned_voice_id") or el.voice,
            "speed": el.speed,
            "encoding": el.output_format,
            "sample_rate": el.sample_rate,
            "stability": el.stability,
            "similarity_boost": el.similarity_boost,
            "base_url": el.base_url,
            # 只报布尔，绝不回传 Key 明文
            "api_key_set": bool(el.api_key.strip()),
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
    previous = _runtime_config.get("tts_provider", "minimax")
    _runtime_config["tts_provider"] = provider
    # 音色 ID 不跨厂商通用（MiniMax 克隆 ID / 火山 S_xxx / ElevenLabs voice_id 各成体系），
    # 切换厂商时必须清掉上一个厂商残留的音色，否则新适配器会拿旧 ID 去请求而 404
    if previous != provider and _runtime_config.get("cloned_voice_id"):
        logger.info(
            f"[config] 切换 TTS 提供商，清除跨厂商残留音色: {_runtime_config['cloned_voice_id']}"
        )
        _runtime_config["cloned_voice_id"] = ""
    logger.info(f"[config] TTS 提供商已切换: {provider}")


def get_elevenlabs_model() -> str:
    """获取 ElevenLabs 生效的 model_id（前端运行时覆盖优先，否则用 .env 默认）"""
    from config.settings import settings
    rt = _runtime_config.get("elevenlabs_model")
    return ((rt or settings.elevenlabs.model_id) or "").strip()


def set_elevenlabs_model(model_id: str):
    """设置 ElevenLabs model_id（运行时热更新，重启恢复 .env 默认）"""
    _runtime_config["elevenlabs_model"] = (model_id or "").strip() or None
    logger.info(f"[config] ElevenLabs 模型已切换: {get_elevenlabs_model()}")


def get_room_id() -> str:
    """获取当前直播间号（前端运行时覆盖优先，否则用 .env 默认）"""
    from config.settings import settings
    return (_runtime_config.get("room_id") or settings.live_room.room_id or "").strip()


def set_room_id(room_id: str):
    """设置当前直播间号（运行时热更新）"""
    _runtime_config["room_id"] = (room_id or "").strip()
    logger.info(f"[config] 直播间号已更新: room_id={_runtime_config['room_id']}")


def _persist_env(key: str, value: str):
    """把运行时配置写回 .env（重启后保留）；失败仅告警不阻断热更新"""
    try:
        from dotenv import set_key
        set_key(".env", key, value)
    except Exception as e:
        logger.warning(f"[config] 写回 .env 失败({key}): {type(e).__name__}: {e}")


@router.put("/tts-provider")
async def update_tts_provider(request: TTSProviderUpdateRequest):
    """切换 TTS 提供商（运行时热更新）"""
    if request.provider not in _SUPPORTED_TTS_PROVIDERS:
        return {
            "status": "error",
            "message": f"不支持的 TTS 提供商，可选: {' / '.join(_SUPPORTED_TTS_PROVIDERS)}",
        }
    set_tts_provider(request.provider)
    return {"status": "ok", "message": f"TTS 提供商已切换为: {request.provider}"}


@router.put("/elevenlabs")
async def update_elevenlabs_config(request: ElevenLabsConfigUpdateRequest):
    """更新 ElevenLabs 运行时配置（model_id 热更新，重启恢复 .env 默认）"""
    if request.model_id is not None and not request.model_id.strip():
        return {"status": "error", "message": "model_id 不能为空"}
    if request.model_id is not None:
        set_elevenlabs_model(request.model_id)
    return {
        "status": "ok",
        "message": "ElevenLabs 配置已更新",
        "model_id": get_elevenlabs_model(),
    }


@router.put("/context-text")
async def update_context_text(request: ContextTextUpdateRequest):
    """更新火山引擎整体语言指令（context_texts，合成时全局生效，预置+复刻音色均适用）"""
    set_context_text(request.context_text)
    return {"status": "ok", "message": "整体语言指令已更新", "context_text": get_context_text()}


@router.get("/room-id")
async def read_room_id():
    """获取当前直播间号 ROOM_ID（运行时生效值 + WebSocket 基础地址）"""
    from config.settings import settings
    return {
        "room_id": get_room_id(),
        "ws_url": settings.live_room.platform_ws_url,
    }


@router.put("/room-id")
async def update_room_id(request: RoomIdUpdateRequest):
    """更新直播间号（运行时热更新 + 写回 .env + 弹幕采集器用新房间号重连）"""
    room_id = (request.room_id or "").strip()
    if not room_id:
        return {"status": "error", "message": "直播间号不能为空"}

    set_room_id(room_id)
    _persist_env("ROOM_ID", room_id)

    # 同步更新 settings，保证同进程内其他直接读 settings.live_room.room_id 的点一致
    from config.settings import settings
    settings.live_room.room_id = room_id

    reconnected = False
    if request.reconnect:
        try:
            from scheduler.collectors.danmaku_collector import DanmakuCollector
            await DanmakuCollector.get_instance().reconnect()
            reconnected = True
        except Exception as e:
            logger.warning(f"[config] 触发弹幕采集器重连失败: {type(e).__name__}: {e}")

    return {
        "status": "ok",
        "message": f"直播间号已更新为 {room_id}" + ("，弹幕监听正在用新房间号重连" if reconnected else ""),
        "room_id": room_id,
        "reconnected": reconnected,
    }


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


@router.get("/viewer-pool")
async def get_viewer_pool():
    """查看实时观众昵称池（真实互动池 + 兜底池），供前端监控看板轮询"""
    from scheduler.viewer_pool import ViewerPool
    return ViewerPool.get_instance().get_pool_status()


@router.post("/viewer-pool/clear")
async def clear_viewer_pool():
    """清空真实互动昵称池（换直播间时重置；不影响兜底名称池）"""
    from scheduler.viewer_pool import ViewerPool
    ViewerPool.get_instance().clear()
    return {
        "status": "ok",
        "message": "真实互动昵称池已清空",
        "data": ViewerPool.get_instance().get_pool_status(),
    }
