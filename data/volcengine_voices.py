"""
火山引擎（豆包 Seed-TTS）系统音色列表

仅保留两类可用音色：
- seed-tts-2.0（预置语音合成）：{语种}_{性别}_{名字}_uranus_bigtts 格式
- seed-icl-2.0（声音复刻）：S_ 开头，控制台训练音频后系统生成

其余音色（BV*_streaming、moon_bigtts、mars_bigtts 等）暂不支持，不列入
"""

# seed-tts-2.0 预置语音合成音色（uranus 系列大模型音色）
URANUS_VOICES = [
    # 中文女声
    {"voice_id": "zh_female_vivi_uranus_bigtts", "name": "Vivi", "gender": "女", "style": "通用"},
    {"voice_id": "zh_female_xiaohe_uranus_bigtts", "name": "小何", "gender": "女", "style": "温柔"},
    {"voice_id": "zh_female_linjianvhai_uranus_bigtts", "name": "邻家女孩", "gender": "女", "style": "清纯"},
    {"voice_id": "zh_female_gaolengyujie_uranus_bigtts", "name": "高冷御姐", "gender": "女", "style": "高冷"},
    {"voice_id": "zh_female_shuangkuaisisi_uranus_bigtts", "name": "爽快思思", "gender": "女", "style": "爽快"},
    {"voice_id": "zh_female_dacey_uranus_bigtts", "name": "Dacey", "gender": "女", "style": "通用"},
    # 中文男声
    {"voice_id": "zh_male_m191_uranus_bigtts", "name": "云舟", "gender": "男", "style": "成熟"},
    {"voice_id": "zh_male_taocheng_uranus_bigtts", "name": "小天", "gender": "男", "style": "阳光"},
    {"voice_id": "zh_male_tim_uranus_bigtts", "name": "Tim", "gender": "男", "style": "通用"},
    # 英文音色
    {"voice_id": "en_female_bella_uranus_bigtts", "name": "Bella", "gender": "女", "style": "通用"},
]


def get_all_volcengine_voices() -> dict:
    """
    返回按场景分组的火山引擎音色列表
    """
    return {
        "预置语音合成（uranus 大模型）": URANUS_VOICES,
    }


def get_volcengine_all_voice_list() -> list[dict]:
    """返回所有火山引擎系统音色的扁平列表"""
    return list(URANUS_VOICES)
