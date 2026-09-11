from fastapi import APIRouter
from utils.volcengine_speaker_utils import get_available_seed_tts2_speakers

router = APIRouter(prefix="/tts/debug", tags=["TTS调试工具"])

# @router.get("/speakers/all")
# async def get_all_speakers():
#     """获取账号全部授权音色（大模型+小模型）"""
#     # 由于API端点可能不可用，暂时注释掉此功能
#     data = get_available_speakers_by_type()
#     return data
# 
# @router.get("/speakers/seed-tts2")
# async def get_seed_tts2_speakers():
#     """仅获取 Seed‑TTS‑2.0 大模型 _bigtts 音色列表"""
#     # 由于API端点可能不可用，暂时注释掉此功能
#     data = get_available_speakers_by_type()
#     bigtts_speakers = data.get("bigtts", [])
#     return {
#         "resource_id": "seed-tts-2.0",
#         "total": len(bigtts_speakers),
#         "speakers": bigtts_speakers
#     }

# 提供一个简化版本，仅返回本地分类音色
@router.get("/speakers/seed-tts2")
async def get_seed_tts2_speakers():
    """获取支持seed-tts-2.0集群的大模型音色列表"""
    speakers = get_available_seed_tts2_speakers()
    return {
        "resource_id": "seed-tts-2.0",
        "total": len(speakers),
        "speakers": speakers
    }

@router.get("/speakers/grouped")
async def get_grouped_speakers():
    """获取按类型分类的音色列表（仅返回seed-tts-2.0兼容的音色）"""
    speakers = get_available_seed_tts2_speakers()
    return {
        "seed-tts-2.0_compatible": speakers,
        "total": len(speakers)
    }