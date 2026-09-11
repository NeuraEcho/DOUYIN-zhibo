import requests
from typing import List, Dict, Optional
import os
from config.settings import settings


def validate_speaker_resource_pair(speaker: str, resource_id: str) -> bool:
    """
    验证音色与Resource ID的配对是否正确
    :param speaker: 音色ID
    :param resource_id: 资源ID
    :return: 配对是否正确
    """
    # 获取推荐的Resource ID
    recommended_resource_id = get_recommended_resource_id(speaker)
    return recommended_resource_id == resource_id


def get_recommended_resource_id(speaker: str) -> str:
    """
    根据音色ID获取推荐的Resource ID
    :param speaker: 音色ID
    :return: 推荐的Resource ID
    """
    # 声音复刻音色
    if speaker.startswith('S_'):
        return "seed-icl-2.0"
    # 豆包语音合成模型 2.0 音色（带_bigtts或_moon_bigtts后缀）
    elif '_bigtts' in speaker or '_moon_bigtts' in speaker:
        return "seed-tts-2.0"
    # 普通流式音色（BV*_*streaming等）使用普通TTS集群
    else:
        return "volcano_tts"


def get_available_seed_tts2_speakers() -> List[Dict]:
    """
    获取支持seed-tts-2.0集群的大模型音色列表
    :return: 支持seed-tts-2.0集群的音色列表
    """
    try:
        # 从现有的音色数据文件获取
        from data.volcengine_voices import SYSTEM_VOICES
        
        # 只筛选支持seed-tts-2.0集群的大模型音色
        seed_tts2_speakers = []
        
        for voice in SYSTEM_VOICES:
            voice_id = voice.get("voice_id", "")
            if voice_id.endswith("_bigtts") or voice_id.endswith("_moon_bigtts"):
                seed_tts2_speakers.append({
                    "SpeakerID": voice_id,
                    "SpeakerName": voice.get("name", ""),
                    "Tags": [voice.get("category", "")],
                    "RecommendedResourceID": "seed-tts-2.0",
                    "Description": "豆包语音合成模型2.0大模型音色"
                })
        
        return seed_tts2_speakers
    except ImportError:
        # 如果导入失败，返回空列表
        return []


def get_compatible_resource_ids() -> Dict[str, List[str]]:
    """
    获取音色类型与Resource ID的兼容性映射
    :return: 兼容性映射字典
    """
    return {
        "bigtts_speakers": ["seed-tts-2.0"],
        "streaming_speakers": ["volcano_tts"],
        "icl_speakers": ["seed-icl-2.0"],
        "all_compatible_pairs": {
            "_bigtts or _moon_bigtts suffix": "seed-tts-2.0",
            "_streaming suffix": "volcano_tts", 
            "S_ prefix": "seed-icl-2.0"
        }
    }


def check_account_resources_access() -> Dict[str, bool]:
    """
    检查账户对各种资源的访问权限
    注意：这是一个模拟检查，实际需要通过API验证
    :return: 各种资源的访问权限状态
    """
    # 由于API端点可能不存在或需要特殊权限，这里提供一个模拟检查
    # 实际情况下，如果TTS调用成功，则说明相应的资源权限可用
    return {
        "seed-tts-2.0": False,  # 需要实际调用测试
        "volcano_tts": False,   # 需要实际调用测试
        "seed-icl-2.0": False,  # 需要实际调用测试
        "notes": "权限状态需要通过实际TTS调用验证"
    }