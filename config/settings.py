"""
统一配置中心 - 所有 API 密钥、模型参数、运行时配置集中管理
通过 pydantic-settings 从 .env 文件加载，支持运行时热更新
"""

from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 强制用 .env 文件覆盖系统环境变量（解决系统环境变量残留旧值问题）
load_dotenv(".env", override=True)


class DeepSeekSettings(BaseSettings):
    """DeepSeek-V4-Flash 模型配置"""
    model_config = SettingsConfigDict(env_prefix="DEEPSEEK_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key: str = ""
    base_url: str = "https://api.openai-proxy.org/v1"
    model: str = "deepseek-v4-flash"
    max_tokens: int = 2048
    temperature: float = 0.7
    timeout: int = 120


class SpeechSettings(BaseSettings):
    """Speech-2.8-Turbo TTS 配置（MiniMax 原生 API）"""
    model_config = SettingsConfigDict(env_prefix="SPEECH_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key: str = ""
    model: str = "speech-2.8-turbo"
    voice: str = "female-shaonv"
    speed: float = 0.92          # 0.90-0.95 略慢更自然，不要 1.0
    pitch: int = -1              # -1~-2 略降音调更真人
    emotion: str = "happy"       # happy/surprised/calm
    sample_rate: int = 24000


class VolcengineSettings(BaseSettings):
    """火山引擎语音合成配置（豆包 Seed-TTS V3 双向流式 WebSocket）"""
    model_config = SettingsConfigDict(env_prefix="VOLCENGINE_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_id: str = ""              # 兼容字段，新版鉴权真正生效的是 api_key
    api_key: str = ""             # 新版控制台 API Key
    cluster: str = "volcano_tts"
    voice_type: str = "BV001_streaming"
    encoding: str = "pcm"         # V3 双向流式原生输出 PCM 裸音频
    speed_ratio: float = 1.0      # 0.5-2.0
    volume_ratio: float = 1.0     # 0.5-2.0
    pitch_ratio: float = 1.0      # 0.5-2.0
    sample_rate: int = 24000

    # ===== Seed-TTS 2.0 情绪合成配置（整体情绪 context_texts，不再逐块套标签）=====
    model: str = "seed-tts-2.0-expressive"   # expressive 支持情绪控制（context_texts 整体情绪）；standard 不支持
    emotion_enabled: bool = True              # 是否下发整体情绪指令 context_texts
    context_text: str = "专业电商主播，情绪自然起伏，口语松弛，真人直播节奏，不要机器感，不要播音腔"  # 整体情绪/全局语音指令（预置 + 复刻2.0 音色均生效）
    block_gap: float = 0.3                    # 分片之间的静音间隔秒数（模拟真人换气），0 关闭


class ElevenLabsSettings(BaseSettings):
    """ElevenLabs TTS 配置（REST 流式接口 /v1/text-to-speech/{voice_id}/stream）

    音频契约（不满足则下游播放变噪声/变调，适配器会在合成前硬拦）：
    - output_format 只能是 pcm_*：AudioPlayer 按 16-bit 单声道裸 PCM 消费，mp3/opus 会变噪声
    - sample_rate 由 output_format 自动推导，需与 AUDIO_SAMPLE_RATE 保持一致
    - pcm_44100 需 ElevenLabs Pro 及以上套餐，默认 pcm_24000 规避该门槛
    """
    model_config = SettingsConfigDict(env_prefix="ELEVENLABS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key: str = ""                            # xi-api-key，控制台 API Keys 页获取
    base_url: str = "https://api.elevenlabs.io"  # 国内直连不通时可改成自建中转地址
    voice: str = "21m00Tcm4TlvDq8ikWAM"          # 默认音色 ID（Rachel）；建议在后台音色列表改选
    model_id: str = "eleven_multilingual_v2"     # 中文必须 multilingual 系列或 flash_v2_5
    language_code: str = ""                      # ISO 639-1；multilingual_v2 不支持此参数，留空
    output_format: str = "pcm_24000"             # 必须是 pcm_*
    sample_rate: int = 24000                     # 由 output_format 自动同步，无需手填

    # ===== voice_settings（0-1）=====
    stability: float = 0.45          # 略低 → 情绪起伏更丰富，直播听感更像真人
    similarity_boost: float = 0.8    # 音色相似度
    style: float = 0.0               # >0 明显增加延迟，直播建议保持 0
    use_speaker_boost: bool = True   # 提升音色相似度，略增延迟
    speed: float = 0.95              # 略慢更自然，与 MiniMax / 火山口径一致

    apply_text_normalization: str = "auto"   # auto / on / off
    timeout: float = 60.0                    # 单次合成总超时（秒）
    connect_timeout: float = 10.0            # 建连超时（秒）
    max_retries: int = 2                     # 429 限流 / 5xx / 网络抖动的重试次数

    @model_validator(mode="after")
    def _sync_sample_rate_from_format(self):
        """采样率以 output_format 为唯一事实来源，避免两处配置不一致导致播放变调变速"""
        fmt = (self.output_format or "").strip().lower()
        self.output_format = fmt
        if fmt.startswith("pcm_"):
            try:
                self.sample_rate = int(fmt.split("_", 1)[1])
            except (IndexError, ValueError):
                pass
        return self


class WebSettings(BaseSettings):
    """Web 管理后台服务配置"""
    model_config = SettingsConfigDict(env_prefix="WEB_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False


class LiveRoomSettings(BaseSettings):
    """直播间配置"""
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    room_id: str = ""
    platform_api_key: str = ""
    platform_ws_url: str = ""


class AudioSettings(BaseSettings):
    """音频输出配置"""
    model_config = SettingsConfigDict(env_prefix="AUDIO_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    sample_rate: int = 24000
    channels: int = 1
    buffer_size: int = 1024

    virtual_sound_card_name: str = Field(
        default="CABLE Input (VB-Audio Virtual Cable)",
        alias="VIRTUAL_SOUND_CARD_NAME",
    )


class LangGraphSettings(BaseSettings):
    """LangGraph 编排层配置"""
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    checkpoint_dir: str = "./checkpoints"
    context_window_size: int = 10


class DanmakuSettings(BaseSettings):
    """弹幕打断过滤配置"""
    model_config = SettingsConfigDict(env_prefix="DANMAKU_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 触发打断的关键词列表（弹幕包含任一关键词即触发打断）
    interrupt_keywords: str = "问一下,多少钱,怎么样,打断,这个,好不好,能用多久,什么时候"
    cooldown_seconds: float = 3.0   # 打断冷却时间（秒），防止刷屏频繁打断
    min_text_length: int = 2        # 忽略字数少于此值的弹幕

    @property
    def keyword_list(self) -> list[str]:
        """将逗号分隔的关键词字符串解析为列表"""
        return [kw.strip() for kw in self.interrupt_keywords.split(",") if kw.strip()]


class ObsSettings(BaseSettings):
    """OBS 推流控制配置（obs-websocket v5 协议，OBS 28+ 内置，无需额外装插件）"""
    model_config = SettingsConfigDict(env_prefix="OBS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    enabled: bool = True                       # 总开关：False 时后台 OBS 卡片提示未启用、接口直接拒绝
    host: str = "127.0.0.1"                    # OBS 与后端同机时保持默认
    port: int = 4455                           # obs-websocket 默认端口
    password: str = ""                         # OBS「工具 → obs-websocket 设置」里的服务器密码；未开鉴权留空
    timeout: float = 8.0                       # 单次「握手 + 请求」的总超时秒数
    stream_service_type: str = "rtmp_custom"   # 直播伴侣发的是自定义推流地址，固定 rtmp_custom


class QAInsertionSettings(BaseSettings):
    """预就绪伺机插入式打断续播配置（脚本播报中的弹幕问答插入）"""
    model_config = SettingsConfigDict(env_prefix="QA_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    enabled: bool = True                    # 总开关，False 时回退旧即时打断
    pregen_timeout_seconds: float = 15.0    # 问答预生成最大超时（超时静默丢弃）
    max_skip_sentences: int = 3             # 回答就绪后最大允许跳过的脚本分句数（超限作废）
    prebuffer_sentences: int = 1            # 脚本合成领先播放的预缓冲句数（逐句即时合成门控）
    answer_trailing_gap_seconds: float = 0.25  # 回答与续读脚本之间的静音间隔


class LogSettings(BaseSettings):
    """日志配置"""
    model_config = SettingsConfigDict(env_prefix="LOG_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    level: str = "INFO"
    file: str = "./logs/live.log"


class Settings(BaseSettings):
    """
    全局配置聚合入口
    使用方式: from config.settings import settings
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    speech: SpeechSettings = Field(default_factory=SpeechSettings)
    volcengine: VolcengineSettings = Field(default_factory=VolcengineSettings)
    elevenlabs: ElevenLabsSettings = Field(default_factory=ElevenLabsSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    live_room: LiveRoomSettings = Field(default_factory=LiveRoomSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    langgraph: LangGraphSettings = Field(default_factory=LangGraphSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    danmaku: DanmakuSettings = Field(default_factory=DanmakuSettings)
    qa_insertion: QAInsertionSettings = Field(default_factory=QAInsertionSettings)
    obs: ObsSettings = Field(default_factory=ObsSettings)

    # 主播默认人设 Prompt（基于真实开发者验证的最佳实践）
    default_system_prompt: str = Field(
        default=(
            "你是抖音带货主播，输出口语化直播话术。\n\n"
            "规则：\n"
            "1. 短句为主，单句控制在20字以内，不要书面长句\n"
            "2. 允许口语助词：哎、哇、对吧、大家看一下\n"
            "3. 数字转中文：99→九十九、100→一百\n"
            "4. 适当插入MiniMax语气词标签：\n"
            "   (breath) 换气、(chuckle) 轻笑【推荐】、(sighs) 叹气\n"
            "   每3-5句最多1个标签，不要连续堆砌\n"
            "5. 不要输出markdown，不要输出标点堆砌\n\n"
            "示例：\n"
            "欢迎刚进来的朋友，\n"
            "(breath)，喜欢可以稍微停留一会。\n"
            "有家人问尺码对吧，\n"
            "咱们这个尺码，从小码到加大码全都有。\n"
            "身上不挑身材(chuckle)。\n"
            "库存不多哈家人们，\n"
            "拍完这批就要等下一批货了。\n"
            "想要的直接去下方小黄车拍。"
        ),
        alias="DEFAULT_SYSTEM_PROMPT",
    )


# 全局单例
settings = Settings()
