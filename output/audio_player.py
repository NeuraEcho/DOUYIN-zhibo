"""
音频播放器 - 从队列消费音频分片，输出到虚拟声卡
独立异步音频服务，和 LangGraph 完全解耦
"""

import asyncio
import time
from typing import Optional

import numpy as np
from loguru import logger

from config.settings import settings
from output.audio_queue import AudioQueueService
from output.virtual_sound_card import VirtualSoundCard


class AudioPlayer:
    """
    音频播放器（单例）

    职责：
    - 从音频队列持续消费音频分片
    - 执行轻量后处理（音量均衡、停顿静音间隙）
    - 输出到虚拟声卡设备
    - 支持打断时立即停止播放
    """

    _instance: Optional["AudioPlayer"] = None

    def __init__(self):
        self._audio_queue = AudioQueueService.get_instance()
        self._playing = False
        self._sample_rate = settings.audio.sample_rate
        self._virtual_sound_card: Optional[VirtualSoundCard] = None
        # 句子间停顿时长（秒），提升真人主播质感
        self._sentence_gap_seconds: float = 0.2
        # 音量增益系数（1.0 = 原始音量）
        self._gain: float = 1.0
        self._last_sentence_index: int = -1
        # 最后一个已写入的音频分片（供打断时尾音淡出）
        self._last_chunk: bytes = b""
        # 协作式打断：播完当前句子后停止（不在句子中间截断）
        self._finish_sentence_then_stop: bool = False
        # 实时节流时钟：winmm waveOutWrite 异步，需按真实播放时钟限速消费，
        # 否则 current_sentence_index 会远超前实际听到的位置（句尾插入门控失效）
        self._pace_anchor: float = 0.0                # 节流锚点墙钟（monotonic）
        self._pace_written: float = 0.0               # 自锚点起已写入音频总时长（秒）
        self._pace_last_wall: Optional[float] = None  # 上次写入完成墙钟，用于空闲重置
        self._pace_lead: float = 0.35                 # 预缓冲（秒）：写入领先播放量，防 underrun 卡顿

    @classmethod
    def get_instance(cls) -> "AudioPlayer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_virtual_sound_card(self, vsc: VirtualSoundCard):
        """注入虚拟声卡实例"""
        self._virtual_sound_card = vsc

    @property
    def current_sentence_index(self) -> int:
        """当前正在（或最后）播放的句子序号，-1 表示尚未开始播放。
        供脚本 TTS 逐句即时合成的播放进度门控读取。"""
        return self._last_sentence_index

    async def start(self):
        """启动音频播放循环（常驻协程，打断后不退出，等待新会话音频）"""
        self._playing = True
        logger.info("[AudioPlayer] 启动播放循环")

        while self._playing:
            chunk = await self._audio_queue.dequeue()
            if chunk is None:
                # 被唤醒但无数据，检查是否应该停止当前句子
                if self._finish_sentence_then_stop:
                    logger.info("[AudioPlayer] 句子边界停止（协作式打断），硬停止虚拟声卡清空缓冲区")
                    # 关键：硬停止虚拟声卡，清空 waveOut 缓冲区中的旧音频
                    # 否则已写入的音频会继续播放，导致“打不断”
                    if self._virtual_sound_card:
                        self._virtual_sound_card.stop()
                    self._finish_sentence_then_stop = False
                    self._last_sentence_index = -1
                    self._last_chunk = b""
                continue

            try:
                # 检测句子切换：新句子的序号与上一句不同
                is_sentence_boundary = (
                    self._last_sentence_index >= 0
                    and chunk.sentence_index != self._last_sentence_index
                )

                if is_sentence_boundary:
                    # 句子边界检查：如果设置了“播完当前句子后停止”，在此处停下当前句子
                    # 但不退出循环，继续等待新会话的音频
                    if self._finish_sentence_then_stop:
                        logger.info(
                            f"[AudioPlayer] 句子 #{self._last_sentence_index} 播放完毕，"
                            f"协作式停止（丢弃新句子 #{chunk.sentence_index}，硬停止虚拟声卡）"
                        )
                        # 关键：硬停止虚拟声卡，清空 waveOut 缓冲区中的旧音频
                        if self._virtual_sound_card:
                            self._virtual_sound_card.stop()
                        self._finish_sentence_then_stop = False
                        self._last_sentence_index = -1
                        self._last_chunk = b""
                        # 丢弃这个 chunk（属于被打断的旧会话），继续等待
                        continue
                    # 插入句子间短暂静音（模拟真人换气节奏）
                    gap_data = self._generate_silence(self._sentence_gap_seconds)
                    if self._virtual_sound_card:
                        await self._virtual_sound_card.write(gap_data)
                        await self._pace_realtime(gap_data)

                self._last_sentence_index = chunk.sentence_index

                # 音频轻量后处理
                processed_data = self._process_audio(chunk.data)

                # 输出到虚拟声卡
                if self._virtual_sound_card:
                    await self._virtual_sound_card.write(processed_data)
                    self._last_chunk = processed_data
                    # 实时节流：waveOutWrite 异步返回，不限速会让硬件缓冲区堆满整段脚本，
                    # 使 current_sentence_index 远超前真实播放位置、句尾插入点漂移。
                    await self._pace_realtime(chunk.data)

                logger.debug(
                    f"[AudioPlayer] 播放分片: "
                    f"sentence={chunk.sentence_index}, "
                    f"size={len(processed_data)} bytes"
                )

            except Exception as e:
                logger.error(f"[AudioPlayer] 播放异常: {e}")

    async def stop(self):
        """停止播放"""
        self._playing = False
        self._finish_sentence_then_stop = False
        self._last_sentence_index = -1
        self._last_chunk = b""
        self._pace_last_wall = None  # 重置节流时钟，下次播放重新锚定
        logger.info("[AudioPlayer] 播放已停止")

    def set_finish_sentence_then_stop(self):
        """设置协作式停止标志：播完当前句子后停止（不在句子中间截断）"""
        self._finish_sentence_then_stop = True
        self._last_sentence_index = -1  # 复位句子序号，下次任何序号都视为新句子
        self._pace_last_wall = None     # 重置节流时钟
        logger.info("[AudioPlayer] 设置句子边界停止标志")

    async def apply_fade_out(self, duration_ms: int = 100):
        """
        打断时对尾音做短淡出，避免硬切爆音，让声音平缓停下。
        同时复位句子间隙状态，避免下一轮首句多出静音间隙。

        说明：虚拟声卡使用 sd.play(blocking=False)，“后一次写入替换前一次”，
        故这里取最后分片的尾巴加线性衰减包络重新写入，听感上是一次快速淡出。
        """
        self._last_sentence_index = -1
        if not self._virtual_sound_card or not self._last_chunk:
            self._last_chunk = b""
            return
        try:
            n = int(self._sample_rate * duration_ms / 1000)
            audio = np.frombuffer(self._last_chunk, dtype=np.int16).astype(np.float32)
            if audio.size == 0:
                return
            tail = audio[-n:] if audio.size >= n else audio
            envelope = np.linspace(1.0, 0.0, num=tail.size, dtype=np.float32)
            faded = (tail * envelope).astype(np.int16).tobytes()
            await self._virtual_sound_card.write(faded)
            logger.info(f"[AudioPlayer] 尾音淡出 {tail.size} 样本 (~{duration_ms}ms)")
        except Exception as e:
            logger.error(f"[AudioPlayer] 淡出异常: {e}")
        finally:
            self._last_chunk = b""

    def _process_audio(self, data: bytes) -> bytes:
        """
        音频轻量后处理（CPU 运算）

        处理内容：
        - 音量均衡：简单增益归一化
        """
        if self._gain == 1.0:
            return data

        # PCM 16-bit → float → 增益 → clip → 回 PCM
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        audio = audio * self._gain
        audio = np.clip(audio, -32768, 32767)
        return audio.astype(np.int16).tobytes()

    def _generate_silence(self, duration_seconds: float) -> bytes:
        """生成指定时长的静音 PCM 数据"""
        num_samples = int(self._sample_rate * duration_seconds)
        silence = np.zeros(num_samples, dtype=np.int16)
        return silence.tobytes()

    async def _pace_realtime(self, data: bytes):
        """按真实播放时钟节流消费，兼顾「进度对齐」与「不卡顿」。

        winmm waveOutWrite 异步返回（立即入硬件缓冲、后台播放）。若不限速，
        AudioPlayer 会在数秒内抽干软件队列、把整段脚本灌满硬件缓冲区，使
        current_sentence_index（出队前沿）远超前实际听到的位置——脚本 TTS 的播放
        进度门控失效，句尾插入的回答被排到数分钟之后（表现为“提问没有回复”）。

        旧实现「写完一个分片就睡满该分片时长」会卡顿：睡醒时硬件恰好播完，
        才去写下一分片，分片边界必然 underrun；且固定步长 sleep 的定时器误差
        会累积，睡得比真实播放更久。这里改用「累积音频时长 vs 墙钟」对齐：
        写完每个分片后只等到 (锚点 + 已写入总时长 - lead)，使写入始终领先播放
        约 lead 秒，硬件缓冲区维持一小段预缓冲，既不堆积也不 underrun；
        sleep_until 每步重算剩余，误差不累积。
        """
        if not data or not self._virtual_sound_card:
            return
        denom = self._sample_rate * max(1, settings.audio.channels) * 2  # 16-bit PCM
        if denom <= 0:
            return
        dur = len(data) / denom
        now = time.monotonic()
        # 空闲/新会话：距上次写入过久说明队列曾空转，重新锚定，避免恢复后 burst 灌满硬件
        if self._pace_last_wall is None or (now - self._pace_last_wall) > 0.5:
            self._pace_anchor = now
            self._pace_written = 0.0
        self._pace_written += dur
        target = self._pace_anchor + self._pace_written - self._pace_lead
        # sleep_until(target)：分小步以便打断即时响应，每步重算剩余，误差不累积
        while self._playing and not self._finish_sentence_then_stop:
            remain = target - time.monotonic()
            if remain <= 0:
                break
            await asyncio.sleep(min(0.1, remain))
        self._pace_last_wall = time.monotonic()
