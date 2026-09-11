"""
TTS 适配器工厂 - 根据运行时配置动态创建 TTS 适配器实例
支持 MiniMax Speech-2.8 和 火山引擎 Seed-TTS 两种提供商
"""

from loguru import logger

from adapters.base import BaseTTSAdapter
from config.settings import settings


def create_tts_adapter(provider: str = None) -> BaseTTSAdapter:
    """
    根据 TTS 提供商创建对应的适配器实例

    :param provider: TTS 提供商名称，None 则读取运行时配置
    :return: BaseTTSAdapter 实例
    """
    # 确定使用哪个提供商
    if provider is None:
        from web.routers.config_router import get_tts_provider
        provider = get_tts_provider()

    if provider == "volcengine":
        from adapters.volcengine_tts_adapter import VolcengineTTSAdapter
        logger.debug("[TTS Factory] 创建火山引擎 TTS 适配器")
        return VolcengineTTSAdapter()
    else:
        from adapters.speech_adapter import Speech28TurboAdapter
        logger.debug("[TTS Factory] 创建 MiniMax TTS 适配器")
        return Speech28TurboAdapter()


def get_tts_provider_name() -> str:
    """获取当前 TTS 提供商名称"""
    from web.routers.config_router import get_tts_provider
    return get_tts_provider()
