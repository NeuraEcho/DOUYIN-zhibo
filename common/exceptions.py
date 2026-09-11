"""
统一异常定义 - 跨层公共异常类型
"""


class BaseLiveException(Exception):
    """直播系统基础异常"""
    def __init__(self, message: str, code: str = "UNKNOWN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class AdapterException(BaseLiveException):
    """云端 AI 适配器异常（DeepSeek-R1 / Speech-2.8-Turbo 调用失败）"""
    def __init__(self, message: str, adapter_name: str = ""):
        super().__init__(message, code=f"ADAPTER_{adapter_name.upper()}_ERROR")
        self.adapter_name = adapter_name


class DeepSeekAdapterException(AdapterException):
    """DeepSeek-R1 适配器异常"""
    def __init__(self, message: str):
        super().__init__(message, adapter_name="deepseek_r1")


class SpeechAdapterException(AdapterException):
    """Speech-2.8-Turbo 适配器异常"""
    def __init__(self, message: str):
        super().__init__(message, adapter_name="speech_28_turbo")


class SchedulerException(BaseLiveException):
    """调度层异常（事件总线、会话管理、抢占中断）"""
    def __init__(self, message: str):
        super().__init__(message, code="SCHEDULER_ERROR")


class AudioOutputException(BaseLiveException):
    """音频输出层异常"""
    def __init__(self, message: str):
        super().__init__(message, code="AUDIO_OUTPUT_ERROR")


class ConfigException(BaseLiveException):
    """配置异常"""
    def __init__(self, message: str):
        super().__init__(message, code="CONFIG_ERROR")
