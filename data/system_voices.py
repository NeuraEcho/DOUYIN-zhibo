"""
MiniMax 系统音色列表（中文普通话 + 常用英文/日文音色）
数据来源：https://platform.minimaxi.com/docs/faq/system-voice-id
"""

# 中文（普通话）系统音色
CHINESE_VOICES = [
    # ---- v1 基础音色 ----
    {"voice_id": "male-qn-qingse", "name": "青涩青年男声", "gender": "男", "style": "青涩"},
    {"voice_id": "male-qn-jingying", "name": "精英青年男声", "gender": "男", "style": "精英"},
    {"voice_id": "male-qn-badao", "name": "霸道青年男声", "gender": "男", "style": "霸道"},
    {"voice_id": "male-qn-daxuesheng", "name": "青年大学生", "gender": "男", "style": "校园"},
    {"voice_id": "female-shaonv", "name": "少女", "gender": "女", "style": "清纯"},
    {"voice_id": "female-yujie", "name": "御姐", "gender": "女", "style": "成熟"},
    {"voice_id": "female-chengshu", "name": "成熟女性", "gender": "女", "style": "稳重"},
    {"voice_id": "female-tianmei", "name": "甜美女性", "gender": "女", "style": "甜美"},
    # ---- v1 精品音色 (beta) ----
    {"voice_id": "male-qn-qingse-jingpin", "name": "青涩青年(beta)", "gender": "男", "style": "青涩"},
    {"voice_id": "male-qn-jingying-jingpin", "name": "精英青年(beta)", "gender": "男", "style": "精英"},
    {"voice_id": "male-qn-badao-jingpin", "name": "霸道青年(beta)", "gender": "男", "style": "霸道"},
    {"voice_id": "male-qn-daxuesheng-jingpin", "name": "大学生(beta)", "gender": "男", "style": "校园"},
    {"voice_id": "female-shaonv-jingpin", "name": "少女(beta)", "gender": "女", "style": "清纯"},
    {"voice_id": "female-yujie-jingpin", "name": "御姐(beta)", "gender": "女", "style": "成熟"},
    {"voice_id": "female-chengshu-jingpin", "name": "成熟女性(beta)", "gender": "女", "style": "稳重"},
    {"voice_id": "female-tianmei-jingpin", "name": "甜美女性(beta)", "gender": "女", "style": "甜美"},
    # ---- v2 特色音色 ----
    {"voice_id": "clever_boy", "name": "聪明男童", "gender": "男", "style": "童声"},
    {"voice_id": "cute_boy", "name": "可爱男童", "gender": "男", "style": "童声"},
    {"voice_id": "lovely_girl", "name": "萌萌女童", "gender": "女", "style": "童声"},
    {"voice_id": "cartoon_pig", "name": "卡通猪小琪", "gender": "未知", "style": "卡通"},
    {"voice_id": "bingjiao_didi", "name": "病娇弟弟", "gender": "男", "style": "撒娇"},
    {"voice_id": "junlang_nanyou", "name": "俊朗男友", "gender": "男", "style": "温柔"},
    {"voice_id": "chunzhen_xuedi", "name": "纯真学弟", "gender": "男", "style": "青涩"},
    {"voice_id": "lengdan_xiongzhang", "name": "冷淡学长", "gender": "男", "style": "高冷"},
    {"voice_id": "badao_shaoye", "name": "霸道少爷", "gender": "男", "style": "霸道"},
    {"voice_id": "tianxin_xiaoling", "name": "甜心小玲", "gender": "女", "style": "甜美"},
    {"voice_id": "qiaopi_mengmei", "name": "俏皮萌妹", "gender": "女", "style": "俏皮"},
    {"voice_id": "wumei_yujie", "name": "妩媚御姐", "gender": "女", "style": "妩媚"},
    {"voice_id": "diadia_xuemei", "name": "嗲嗲学妹", "gender": "女", "style": "撒娇"},
    {"voice_id": "danya_xuejie", "name": "淡雅学姐", "gender": "女", "style": "淡雅"},
    # ---- v2 场景音色 ----
    {"voice_id": "presenter_male", "name": "男性主持人", "gender": "男", "style": "播报"},
    {"voice_id": "presenter_female", "name": "女性主持人", "gender": "女", "style": "播报"},
    {"voice_id": "audiobook_male_1", "name": "男性有声书1", "gender": "男", "style": "有声书"},
    {"voice_id": "audiobook_male_2", "name": "男性有声书2", "gender": "男", "style": "有声书"},
    {"voice_id": "audiobook_female_1", "name": "女性有声书1", "gender": "女", "style": "有声书"},
    {"voice_id": "audiobook_female_2", "name": "女性有声书2", "gender": "女", "style": "有声书"},
    # ---- v2 高级场景音色 ----
    {"voice_id": "Chinese (Mandarin)_Reliable_Executive", "name": "沉稳高管", "gender": "男", "style": "商务"},
    {"voice_id": "Chinese (Mandarin)_News_Anchor", "name": "新闻女声", "gender": "女", "style": "新闻"},
    {"voice_id": "Chinese (Mandarin)_Mature_Woman", "name": "傲娇御姐", "gender": "女", "style": "傲娇"},
    {"voice_id": "Chinese (Mandarin)_Unrestrained_Young_Man", "name": "不羁青年", "gender": "男", "style": "不羁"},
    {"voice_id": "Arrogant_Miss", "name": "嚣张小姐", "gender": "女", "style": "嚣张"},
    {"voice_id": "Robot_Armor", "name": "机械战甲", "gender": "男", "style": "机械"},
    {"voice_id": "Chinese (Mandarin)_Kind-hearted_Antie", "name": "热心大婶", "gender": "女", "style": "亲切"},
    {"voice_id": "Chinese (Mandarin)_HK_Flight_Attendant", "name": "港普空姐", "gender": "女", "style": "港普"},
    {"voice_id": "Chinese (Mandarin)_Humorous_Elder", "name": "搞笑大爷", "gender": "男", "style": "搞笑"},
    {"voice_id": "Chinese (Mandarin)_Gentleman", "name": "温润男声", "gender": "男", "style": "温润"},
    {"voice_id": "Chinese (Mandarin)_Warm_Bestie", "name": "温暖闺蜜", "gender": "女", "style": "温暖"},
    {"voice_id": "Chinese (Mandarin)_Male_Announcer", "name": "播报男声", "gender": "男", "style": "播报"},
    {"voice_id": "Chinese (Mandarin)_Sweet_Lady", "name": "甜美女声", "gender": "女", "style": "甜美"},
    {"voice_id": "Chinese (Mandarin)_Southern_Young_Man", "name": "南方小哥", "gender": "男", "style": "南方"},
    {"voice_id": "Chinese (Mandarin)_Wise_Women", "name": "阅历姐姐", "gender": "女", "style": "知性"},
    {"voice_id": "Chinese (Mandarin)_Gentle_Youth", "name": "温润青年", "gender": "男", "style": "温润"},
    {"voice_id": "Chinese (Mandarin)_Warm_Girl", "name": "温暖少女", "gender": "女", "style": "温暖"},
    {"voice_id": "Chinese (Mandarin)_Kind-hearted_Elder", "name": "花甲奶奶", "gender": "女", "style": "老年"},
    {"voice_id": "Chinese (Mandarin)_Cute_Spirit", "name": "憨憨萌兽", "gender": "未知", "style": "萌系"},
    {"voice_id": "Chinese (Mandarin)_Radio_Host", "name": "电台男主播", "gender": "男", "style": "电台"},
    {"voice_id": "Chinese (Mandarin)_Lyrical_Voice", "name": "抒情男声", "gender": "男", "style": "抒情"},
    {"voice_id": "Chinese (Mandarin)_Straightforward_Boy", "name": "率真弟弟", "gender": "男", "style": "率真"},
    {"voice_id": "Chinese (Mandarin)_Sincere_Adult", "name": "真诚青年", "gender": "男", "style": "真诚"},
    {"voice_id": "Chinese (Mandarin)_Gentle_Senior", "name": "温柔学姐", "gender": "女", "style": "温柔"},
    {"voice_id": "Chinese (Mandarin)_Stubborn_Friend", "name": "嘴硬竹马", "gender": "男", "style": "傲娇"},
    {"voice_id": "Chinese (Mandarin)_Crisp_Girl", "name": "清脆少女", "gender": "女", "style": "清脆"},
    {"voice_id": "Chinese (Mandarin)_Pure-hearted_Boy", "name": "清澈邻家弟弟", "gender": "男", "style": "清澈"},
    {"voice_id": "Chinese (Mandarin)_Soft_Girl", "name": "柔和少女", "gender": "女", "style": "柔和"},
    # ---- 特色解说/叙事音色 ----
    {"voice_id": "zh_male_jieshuonansheng_mars_bigtts", "name": "磁性解说男声", "gender": "男", "style": "解说"},
    {"voice_id": "zh_female_jitangmeimei_mars_bigtts", "name": "鸡汤妹妹", "gender": "女", "style": "鸡汤"},
    {"voice_id": "zh_female_tiexinnvsheng_mars_bigtts", "name": "贴心女声", "gender": "女", "style": "贴心"},
    {"voice_id": "zh_female_qiaopinvsheng_mars_bigtts", "name": "俏皮女声", "gender": "女", "style": "俏皮"},
    {"voice_id": "zh_female_mengyatou_mars_bigtts", "name": "萌丫头", "gender": "女", "style": "萌系"},
    {"voice_id": "zh_male_changtianyi_mars_bigtts", "name": "悬疑解说", "gender": "男", "style": "悬疑"},
    {"voice_id": "zh_male_ruyaqingnian_mars_bigtts", "name": "儒雅青年", "gender": "男", "style": "儒雅"},
    {"voice_id": "zh_male_baqiqingshu_mars_bigtts", "name": "霸气青叔", "gender": "男", "style": "霸气"},
    {"voice_id": "zh_male_qingcang_mars_bigtts", "name": "擎苍", "gender": "男", "style": "沧桑"},
    {"voice_id": "zh_male_yangguangqingnian_mars_bigtts", "name": "阳光青年", "gender": "男", "style": "阳光"},
    {"voice_id": "zh_female_gufengshaoyu_mars_bigtts", "name": "古风少御", "gender": "女", "style": "古风"},
    {"voice_id": "zh_female_wenroushunv_mars_bigtts", "name": "温柔淑女", "gender": "女", "style": "温柔"},
]

# 中文（粤语）系统音色
CANTONESE_VOICES = [
    {"voice_id": "Cantonese_ProfessionalHost（F)", "name": "专业女主持(粤)", "gender": "女", "style": "播报"},
    {"voice_id": "Cantonese_GentleLady", "name": "温柔女声(粤)", "gender": "女", "style": "温柔"},
    {"voice_id": "Cantonese_ProfessionalHost（M)", "name": "专业男主持(粤)", "gender": "男", "style": "播报"},
    {"voice_id": "Cantonese_PlayfulMan", "name": "活泼男声(粤)", "gender": "男", "style": "活泼"},
    {"voice_id": "Cantonese_CuteGirl", "name": "可爱女孩(粤)", "gender": "女", "style": "可爱"},
    {"voice_id": "Cantonese_KindWoman", "name": "善良女声(粤)", "gender": "女", "style": "善良"},
]

# 英文系统音色
ENGLISH_VOICES = [
    {"voice_id": "Sweet_Girl", "name": "Sweet Girl", "gender": "女", "style": "甜美"},
    {"voice_id": "Cute_Elf", "name": "Cute Elf", "gender": "女", "style": "可爱"},
    {"voice_id": "Attractive_Girl", "name": "Attractive Girl", "gender": "女", "style": "迷人"},
    {"voice_id": "Serene_Woman", "name": "Serene Woman", "gender": "女", "style": "宁静"},
    {"voice_id": "English_Trustworthy_Man", "name": "Trustworthy Man", "gender": "男", "style": "可信"},
    {"voice_id": "English_Graceful_Lady", "name": "Graceful Lady", "gender": "女", "style": "优雅"},
    {"voice_id": "English_Aussie_Bloke", "name": "Aussie Bloke", "gender": "男", "style": "澳洲"},
    {"voice_id": "English_Whispering_girl", "name": "Whispering Girl", "gender": "女", "style": "低语"},
    {"voice_id": "English_Diligent_Man", "name": "Diligent Man", "gender": "男", "style": "勤勉"},
    {"voice_id": "English_Gentle-voiced_man", "name": "Gentle-voiced Man", "gender": "男", "style": "温和"},
]


def get_all_system_voices() -> dict:
    """
    返回按语言分组的系统音色列表
    """
    return {
        "中文普通话": CHINESE_VOICES,
        "中文粤语": CANTONESE_VOICES,
        "英文": ENGLISH_VOICES,
    }


def get_chinese_voices() -> list[dict]:
    """返回中文普通话音色列表"""
    return CHINESE_VOICES
