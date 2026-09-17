"""
ElevenLabs 预置音色静态兜底列表

优先级：services/elevenlabs_voice_service.py 会先调 GET /v1/voices 拉取账号下的真实音色
（含你在 elevenlabs.io 官网创建的克隆音色）；仅当 API 不可达（未配 Key / 无网络 / 被墙）
时才回退到这张表，保证后台下拉框不至于空白。

⚠️ voice_id 由 ElevenLabs 分配，不同账号 / 不同套餐可见的预置音色集合可能不同，
   一律以在线列表为准；用兜底表里的 ID 若返回 404，说明你的账号没有该音色，
   请到「音色克隆」页点「刷新在线列表」重选。
"""

# 官方预置音色（长期稳定的 premade 集合）
PREMADE_VOICES = [
    # 女声
    {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "gender": "女", "style": "沉稳叙述"},
    {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah", "gender": "女", "style": "柔和亲和"},
    {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Bella", "gender": "女", "style": "明亮年轻"},
    {"voice_id": "9BWtsMINqrJLrRacOk9x", "name": "Aria", "gender": "女", "style": "活力口语"},
    {"voice_id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Alice", "gender": "女", "style": "轻快"},
    {"voice_id": "cgSgspJ2msm6clMCkdW9", "name": "Jessica", "gender": "女", "style": "开朗"},
    {"voice_id": "CwhRBWXzGAHq8TQ4Fs17", "name": "Freya", "gender": "女", "style": "清亮"},
    {"voice_id": "pFZP5JQG7iQjIQuC4Bku", "name": "Lily", "gender": "女", "style": "温暖"},
    {"voice_id": "XB0fDUnXU5powFXDhCwa", "name": "Charlotte", "gender": "女", "style": "优雅"},
    {"voice_id": "aawNSm8twCdsutVU2j7o", "name": "Megan", "gender": "女", "style": "自然"},
    {"voice_id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli", "gender": "女", "style": "年轻"},
    {"voice_id": "jBpfuIE2acCO8z3wKNLl", "name": "Gigi", "gender": "女", "style": "甜美"},
    # 男声
    {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "George", "gender": "男", "style": "沉稳"},
    {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh", "gender": "男", "style": "低沉"},
    {"voice_id": "iP95p0xoKVk53GoZ742B", "name": "Chris", "gender": "男", "style": "活力"},
    {"voice_id": "nPppLsKlG18nMfjS6Q8V", "name": "Brian", "gender": "男", "style": "磁性"},
    {"voice_id": "IKne3meq5aSn9XLyUdCD", "name": "Charlie", "gender": "男", "style": "英式"},
    {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel", "gender": "男", "style": "权威播报"},
    {"voice_id": "yoZ06aMxZJJ28mfd3POQ", "name": "Sam", "gender": "男", "style": "沙哑"},
    {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni", "gender": "男", "style": "干净"},
    {"voice_id": "VR6AewLTigWG4xSOukaG", "name": "Arnold", "gender": "男", "style": "强硬"},
]


def get_all_elevenlabs_voices() -> dict:
    """返回按类别分组的 ElevenLabs 兜底音色列表（结构与在线接口一致）"""
    return {"官方预置音色（离线兜底）": list(PREMADE_VOICES)}


def get_elevenlabs_all_voice_list() -> list[dict]:
    """返回所有兜底音色的扁平列表"""
    return list(PREMADE_VOICES)
