"""
音色克隆路由 - 上传参考音频、克隆音色、管理克隆音色列表
"""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel
from typing import Optional

from services.voice_clone_service import VoiceCloneService

router = APIRouter()


# ---------- 请求模型 ----------

class CloneVoiceRequest(BaseModel):
    """克隆音色请求"""
    voice_id: Optional[str] = None  # 不填则自动生成
    name: str  # 音色显示名称
    file_id: str  # 参考音频 file_id（由上传接口返回）
    prompt_file_id: Optional[str] = None  # 示例音频 file_id（可选）
    prompt_text: Optional[str] = None  # 示例音频对应文本（可选）
    text: str = ""  # 试听文本
    model: str = "speech-2.8-hd"  # 高保真模型
    language_boost: str = "Chinese"  # 语言增强
    need_noise_reduction: bool = True  # 降噪
    need_volume_normalization: bool = True  # 音量归一化


class UseVoiceRequest(BaseModel):
    """设置当前使用的克隆音色"""
    voice_id: str


class VolcCloneQueryRequest(BaseModel):
    """火山引擎复刻音色查询/注册请求"""
    speaker_id: str          # S_ 开头的唯一音色代号（控制台获取）
    name: str = ""           # 可选显示名称


class VolcCloneUseRequest(BaseModel):
    """设置当前使用的火山复刻音色"""
    speaker_id: str


# ---------- API 接口 ----------

@router.post("/upload-clone-audio")
async def upload_clone_audio(file: UploadFile = File(...)):
    """
    上传待克隆的参考音频

    要求：
    - 格式：mp3 / m4a / wav
    - 时长：10秒 ~ 5分钟
    - 大小：≤ 20MB
    """
    service = VoiceCloneService.get_instance()
    file_data = await file.read()

    try:
        file_id = await service.upload_clone_audio(file.filename or "audio.wav", file_data)
        return {"status": "ok", "file_id": file_id, "filename": file.filename}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[voice_clone] 上传参考音频失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


@router.post("/upload-prompt-audio")
async def upload_prompt_audio(file: UploadFile = File(...)):
    """
    上传示例音频（可选，用于增强克隆效果）

    要求：
    - 格式：mp3 / m4a / wav
    - 时长：< 8秒
    - 大小：≤ 20MB
    """
    service = VoiceCloneService.get_instance()
    file_data = await file.read()

    try:
        file_id = await service.upload_prompt_audio(file.filename or "prompt.wav", file_data)
        return {"status": "ok", "file_id": file_id, "filename": file.filename}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[voice_clone] 上传示例音频失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


@router.post("/clone")
async def clone_voice(request: CloneVoiceRequest):
    """
    执行音色克隆

    基于已上传的参考音频 file_id，调用 MiniMax 克隆接口生成新音色。
    克隆完成后可在 TTS 中使用返回的 voice_id。
    """
    service = VoiceCloneService.get_instance()

    # 自动生成 voice_id
    voice_id = request.voice_id or service.generate_voice_id(request.name)

    try:
        result = await service.clone_voice(
            voice_id=voice_id,
            name=request.name,
            file_id=request.file_id,
            prompt_file_id=request.prompt_file_id,
            prompt_text=request.prompt_text,
            text=request.text,
            model=request.model,
            language_boost=request.language_boost,
            need_noise_reduction=request.need_noise_reduction,
            need_volume_normalization=request.need_volume_normalization,
        )
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"[voice_clone] 克隆失败: {e}")
        raise HTTPException(status_code=500, detail=f"克隆失败: {e}")


@router.get("/system-voices")
async def list_system_voices():
    """获取系统音色列表（根据当前 TTS 提供商返回对应音色）"""
    from web.routers.config_router import get_tts_provider

    provider = get_tts_provider()
    if provider == "volcengine":
        # 火山引擎：动态从 API 获取音色列表
        from services.volcengine_voice_service import get_volcengine_voices
        voices = await get_volcengine_voices()
    else:
        # MiniMax：使用本地静态文件
        from data.system_voices import get_all_system_voices
        voices = get_all_system_voices()

    total = sum(len(v) for v in voices.values())
    return {"voices": voices, "total": total, "provider": provider}


@router.get("/voices")
async def list_voices():
    """获取所有已克隆音色列表"""
    service = VoiceCloneService.get_instance()
    voices = service.list_voices()
    return {"voices": voices, "total": len(voices)}


@router.get("/voices/{voice_id}")
async def get_voice(voice_id: str):
    """获取指定音色详情"""
    service = VoiceCloneService.get_instance()
    voice = service.get_voice(voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail=f"音色不存在: {voice_id}")
    return voice


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    """删除指定克隆音色"""
    service = VoiceCloneService.get_instance()
    if not service.delete_voice(voice_id):
        raise HTTPException(status_code=404, detail=f"音色不存在: {voice_id}")
    return {"status": "ok", "message": f"音色 {voice_id} 已删除"}


@router.post("/use")
async def use_voice(request: UseVoiceRequest):
    """
    设置当前 TTS 使用的克隆音色

    设置后，后续所有 TTS 合成将使用该克隆音色而非默认音色。
    """
    from web.routers.config_router import set_cloned_voice

    service = VoiceCloneService.get_instance()
    voice = service.get_voice(request.voice_id)
    if not voice:
        raise HTTPException(status_code=404, detail=f"音色不存在: {request.voice_id}")

    set_cloned_voice(request.voice_id)
    logger.info(f"[voice_clone] 已切换 TTS 音色: {request.voice_id} ({voice['name']})")
    return {"status": "ok", "message": f"已切换到音色: {voice['name']}", "voice_id": request.voice_id}


# ============================================================
# 火山引擎声音复刻（克隆）音色管理
# 依据官方「音色查询HTTP」接口 doc 6561/2535742：
#   POST https://openspeech.bytedance.com/api/v3/tts/get_voice
# ============================================================

@router.get("/volcengine/voices")
async def list_volc_cloned_voices():
    """获取火山引擎已注册的复刻（克隆）音色列表"""
    from services.volcengine_voice_service import list_volcengine_cloned_voices
    voices = list_volcengine_cloned_voices()
    return {"voices": voices, "total": len(voices)}


@router.post("/volcengine/query")
async def query_volc_cloned_voice(request: VolcCloneQueryRequest):
    """查询并注册一个火山引擎复刻音色（按 speaker_id 查询训练状态）"""
    from services.volcengine_voice_service import query_volcengine_cloned_voice as _query
    try:
        record = await _query(request.speaker_id, request.name)
        return {"status": "ok", "voice": record}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[voice_clone] 火山复刻音色查询失败: {e}")
        raise HTTPException(status_code=502, detail=f"查询失败: {e}")


@router.post("/volcengine/refresh")
async def refresh_volc_cloned_voices():
    """刷新全部已注册火山复刻音色的状态"""
    from services.volcengine_voice_service import refresh_volcengine_cloned_voices as _refresh
    try:
        voices = await _refresh()
        return {"status": "ok", "voices": voices, "total": len(voices)}
    except Exception as e:
        logger.error(f"[voice_clone] 刷新火山复刻音色失败: {e}")
        raise HTTPException(status_code=502, detail=f"刷新失败: {e}")


@router.delete("/volcengine/voices/{speaker_id}")
async def delete_volc_cloned_voice(speaker_id: str):
    """删除（从本地列表移除）指定火山复刻音色"""
    from services.volcengine_voice_service import delete_volcengine_cloned_voice as _delete
    if not _delete(speaker_id):
        raise HTTPException(status_code=404, detail=f"复刻音色不存在: {speaker_id}")
    return {"status": "ok", "message": f"复刻音色 {speaker_id} 已删除"}


@router.post("/volcengine/use")
async def use_volc_cloned_voice(request: VolcCloneUseRequest):
    """设置当前 TTS 使用火山复刻音色（speaker_id 作为克隆音色）"""
    from web.routers.config_router import set_cloned_voice
    from services.volcengine_voice_service import get_volcengine_cloned_voice

    voice = get_volcengine_cloned_voice(request.speaker_id)
    if not voice:
        raise HTTPException(status_code=404, detail=f"复刻音色不存在: {request.speaker_id}")
    if not voice.get("usable"):
        raise HTTPException(
            status_code=400,
            detail=f"音色当前状态不可用: {voice.get('status_text')}（仅训练成功/已激活可用）",
        )
    set_cloned_voice(request.speaker_id)
    logger.info(f"[voice_clone] 已切换火山复刻音色: {request.speaker_id} ({voice.get('name')})")
    return {
        "status": "ok",
        "message": f"已切换到复刻音色: {voice.get('name')}",
        "speaker_id": request.speaker_id,
    }
