"""
虚拟声卡输出驱动 - 将音频流输出到系统虚拟音频设备

使用 Windows winmm (MME) API 直接输出音频，绕过 sounddevice/PortAudio 的 DirectSound 兼容性问题。
winmm 是 Windows 最底层的音频播放 API，VB-Audio Virtual Cable 保证支持。
"""

import ctypes
import ctypes.wintypes as wintypes
import numpy as np
from loguru import logger
import threading

from config.settings import settings

# ──────────── Windows winmm (MME) 常量与结构 ────────────
WAVE_MAPPER = -1
WHDR_DONE = 0x00000001
MMRESULT = wintypes.UINT


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR_PTR = ctypes.POINTER(WAVEHDR)

WAVEHDR._fields_ = [
    ("lpData", ctypes.c_char_p),
    ("dwBufferLength", wintypes.DWORD),
    ("dwBytesRecorded", wintypes.DWORD),
    ("dwUser", ctypes.c_void_p),
    ("dwFlags", wintypes.DWORD),
    ("dwLoops", wintypes.DWORD),
    ("lpNext", WAVEHDR_PTR),
    ("reserved", wintypes.DWORD),
]


# 加载 winmm.dll
_winmm = ctypes.WinDLL("winmm")
_winmm.waveOutOpen.argtypes = [
    ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
]
_winmm.waveOutOpen.restype = MMRESULT
_winmm.waveOutWrite.argtypes = [ctypes.c_void_p, WAVEHDR_PTR, ctypes.c_uint]
_winmm.waveOutWrite.restype = MMRESULT
_winmm.waveOutPrepareHeader.argtypes = [ctypes.c_void_p, WAVEHDR_PTR, ctypes.c_uint]
_winmm.waveOutPrepareHeader.restype = MMRESULT
_winmm.waveOutUnprepareHeader.argtypes = [ctypes.c_void_p, WAVEHDR_PTR, ctypes.c_uint]
_winmm.waveOutUnprepareHeader.restype = MMRESULT
_winmm.waveOutReset.argtypes = [ctypes.c_void_p]
_winmm.waveOutReset.restype = MMRESULT
_winmm.waveOutClose.argtypes = [ctypes.c_void_p]
_winmm.waveOutClose.restype = MMRESULT
_winmm.waveOutGetNumDevs.restype = ctypes.c_uint

# 也加载 sounddevice 用于设备枚举（仅用于 initialize 时查找设备索引）
import sounddevice as sd


class VirtualSoundCard:
    """
    虚拟声卡输出驱动（winmm 版）

    职责：
    - 管理系统虚拟音频设备（VB-Audio Virtual Cable）
    - 通过 winmm waveOut* API 写入 PCM 音频
    - 设备检测与错误提示
    """

    def __init__(self):
        self._device_name = settings.audio.virtual_sound_card_name
        self._sample_rate = settings.audio.sample_rate
        self._channels = settings.audio.channels
        self._buffer_size = settings.audio.buffer_size
        self._initialized = False

        # winmm 句柄
        self._hwo = ctypes.c_void_p(0)

        # 设备实际参数（初始化时从 sounddevice 获取）
        self._device_channels: int = 1
        self._device_sample_rate: float = 44100.0

        # 防止 header 被 GC 回收（保持引用）
        self._headers: list = []
        self._buffers: list = []
        self._lock = threading.Lock()

    def initialize(self) -> bool:
        """
        初始化虚拟声卡设备

        流程：
        1. 用 sounddevice 枚举设备、按名称匹配 VB-Audio
        2. 用 winmm waveOutOpen 打开设备（WAVE_MAPPER 或指定设备）
        3. 准备好 WAVEFORMATEX 用于后续写入

        :return: 是否成功
        """
        try:
            # 先用 sounddevice 枚举找到设备信息（仅用于日志和参数）
            devices = sd.query_devices()
            logger.info(f"[VirtualSoundCard] 系统音频设备数: {len(devices)}")

            for i, dev in enumerate(devices):
                if dev["max_output_channels"] > 0 and self._device_name.lower() in dev["name"].lower():
                    self._device_channels = min(dev["max_output_channels"], 2)  # winmm 共享模式用 1-2 通道
                    self._device_sample_rate = dev["default_samplerate"]
                    logger.info(
                        f"[VirtualSoundCard] 找到目标设备: "
                        f"index={i}, name={dev['name']}, "
                        f"channels={self._device_channels}, "
                        f"sample_rate={self._device_sample_rate}"
                    )
                    break
            else:
                logger.warning(f"[VirtualSoundCard] 未找到设备: {self._device_name}")
                logger.warning("将使用 WAVE_MAPPER（系统默认输出）")

            # 构造 WAVEFORMATEX
            wf = WAVEFORMATEX()
            wf.wFormatTag = 1  # WAVE_FORMAT_PCM
            wf.nChannels = self._device_channels
            wf.nSamplesPerSec = int(self._device_sample_rate)
            wf.wBitsPerSample = 16
            wf.nBlockAlign = wf.nChannels * wf.wBitsPerSample // 8
            wf.nAvgBytesPerSec = wf.nSamplesPerSec * wf.nBlockAlign
            wf.cbSize = 0

            # 打开 waveOut 设备（WAVE_MAPPER = 系统默认，会路由到 VB-Audio 如果它是默认设备）
            result = _winmm.waveOutOpen(
                ctypes.byref(self._hwo),
                WAVE_MAPPER,
                ctypes.byref(wf),
                0, 0, 0,  # 无回调
            )
            if result != 0:
                logger.error(f"[VirtualSoundCard] waveOutOpen 失败, 错误码: {result}")
                return False

            self._initialized = True
            logger.info(
                f"[VirtualSoundCard] winmm 设备已打开: "
                f"channels={self._device_channels}, "
                f"sample_rate={int(self._device_sample_rate)}, "
                f"bits=16, format=PCM"
            )
            return True

        except Exception as e:
            logger.error(f"[VirtualSoundCard] 初始化失败: {e}")
            logger.error("请确认已安装 VB-Audio Virtual Cable 虚拟声卡驱动")
            return False

    async def write(self, data: bytes):
        """
        写入音频数据到虚拟声卡（通过 winmm waveOutWrite）

        :param data: PCM 16-bit 音频二进制数据
        """
        if not self._initialized:
            logger.warning("[VirtualSoundCard] 设备未初始化")
            return

        try:
            # 如果采样率不匹配，需要重采样（winmm 不做自动重采样）
            if self._sample_rate != int(self._device_sample_rate):
                audio_int16 = np.frombuffer(data, dtype=np.int16)
                ratio = self._device_sample_rate / self._sample_rate
                new_len = int(len(audio_int16) * ratio)
                old_indices = np.linspace(0, len(audio_int16) - 1, len(audio_int16))
                new_indices = np.linspace(0, len(audio_int16) - 1, new_len)
                audio_int16 = np.interp(new_indices, old_indices, audio_int16).astype(np.int16)
                data = audio_int16.tobytes()

            # 如果通道数不匹配（设备是立体声但数据是单声道），扩展
            if self._device_channels == 2:
                audio_int16 = np.frombuffer(data, dtype=np.int16)
                # 单声道 → 立体声（每样本复制一份）
                stereo = np.column_stack([audio_int16, audio_int16]).flatten()
                data = stereo.tobytes()

            # 分配 buffer（保持引用防止 GC）
            buf = ctypes.create_string_buffer(data)
            self._buffers.append(buf)

            # 准备 WAVEHDR
            hdr = WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_char_p)
            hdr.dwBufferLength = len(data)
            hdr.dwFlags = 0
            self._headers.append(hdr)

            # 准备 header
            result = _winmm.waveOutPrepareHeader(self._hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if result != 0:
                logger.error(f"[VirtualSoundCard] waveOutPrepareHeader 失败: {result}")
                return

            # 写入音频
            result = _winmm.waveOutWrite(self._hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
            if result != 0:
                logger.error(f"[VirtualSoundCard] waveOutWrite 失败: {result}")
                return

            # 清理已完成的 header（防止内存泄漏）
            self._cleanup_done_headers()

        except Exception as e:
            logger.error(f"[VirtualSoundCard] 写入异常: {e}")

    def _cleanup_done_headers(self):
        """清理已播放完毕的 header，释放 buffer"""
        remaining_hdrs = []
        remaining_bufs = []
        for hdr, buf in zip(self._headers, self._buffers):
            if hdr.dwFlags & WHDR_DONE:
                _winmm.waveOutUnprepareHeader(self._hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
            else:
                remaining_hdrs.append(hdr)
                remaining_bufs.append(buf)
        self._headers = remaining_hdrs
        self._buffers = remaining_bufs

    def stop(self):
        """立即停止所有待播放的音频（打断时调用）"""
        if self._initialized and self._hwo.value:
            # waveOutReset 停止所有待播音频，未播完的分片直接丢弃
            _winmm.waveOutReset(self._hwo)
            # 清理所有 header
            for hdr in self._headers:
                try:
                    _winmm.waveOutUnprepareHeader(self._hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
                except Exception:
                    pass
            self._headers.clear()
            self._buffers.clear()
            logger.info("[VirtualSoundCard] 播放已停止（waveOutReset）")

    def close(self):
        """关闭设备"""
        if self._initialized:
            # 清理所有未完成的 header
            for hdr in self._headers:
                _winmm.waveOutUnprepareHeader(self._hwo, ctypes.byref(hdr), ctypes.sizeof(hdr))
            self._headers.clear()
            self._buffers.clear()

            _winmm.waveOutReset(self._hwo)
            _winmm.waveOutClose(self._hwo)
            self._initialized = False
            logger.info("[VirtualSoundCard] 设备已关闭")
