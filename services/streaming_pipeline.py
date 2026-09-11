"""
LLM 流式输出 + TTS 流式合成管线
实现真正的边生成边合成边播放，将首音延迟压缩到最低
"""

import asyncio
import random
import re
from contextlib import aclosing
from typing import Optional

from loguru import logger

from adapters.deepseek_adapter import DeepSeekR1Adapter
from config.settings import settings
from graph.state import AudioTask
from services.text_enricher_service import normalize_text_for_tts
from services.tts_factory import create_tts_adapter

# 中英文标点断句字符集合
_DELIMITER_CHARS = set('。！？；\n.!?;')

# MiniMax 情绪标签正则（用于 TTS 前剥离）
_EMOTION_TAG_RE = re.compile(
    r'\((?:breath|chuckle|laughs|sighs|coughs|clear-throat|emm|groans|pant|'
    r'inhale|exhale|gasps|sniffs|snorts|burps|lip-smacking|humming|hissing|sneezes)\)',
    re.IGNORECASE,
)
_PAUSE_TAG_RE = re.compile(r'<#\d+(?:\.\d+)?#>')


def _strip_emotion_tags(text: str) -> str:
    """剥离情绪标签和停顿标记"""
    text = _EMOTION_TAG_RE.sub('', text)
    text = _PAUSE_TAG_RE.sub('', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


class StreamingPipeline:
    """
    LLM 流式 + TTS 流式管线（单例）

    核心原理：
    - 生产者：LLM 流式输出 token，遇到句子边界立即放入队列
    - 消费者：从队列取句子，立即送 TTS 合成并投递音频
    - 两者并发执行，LLM 生成第 2 句时 TTS 已在合成第 1 句
    - 首音延迟 = 首句生成时间 + 首句 TTS 时间（约 200-600ms）
    """

    _instance: Optional["StreamingPipeline"] = None

    def __init__(self):
        self._sentence_queue: asyncio.Queue = asyncio.Queue()
        self._audio_tasks: list[AudioTask] = []
        self._sentence_counter: int = 0
        self._completed_count: int = 0
        self._full_text: str = ""

    @classmethod
    def get_instance(cls) -> "StreamingPipeline":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def run(
        self,
        messages: list[dict],
        system_prompt: str,
        cancel_check=None,
    ) -> dict:
        """
        运行流式管线

        :param messages: API 格式的消息列表
        :param system_prompt: 系统提示词（已包含在 messages 中）
        :param cancel_check: 打断检查回调
        :return: 状态更新字典
        """
        from scheduler.session_manager import SessionManager
        session_manager = SessionManager.get_instance()

        # 重置状态
        self._sentence_queue = asyncio.Queue()
        self._audio_tasks = []
        self._sentence_counter = 0
        self._completed_count = 0
        self._full_text = ""

        # 并发启动生产者和消费者
        producer_task = asyncio.create_task(
            self._llm_producer(messages, cancel_check),
            name="streaming_llm_producer"
        )
        consumer_task = asyncio.create_task(
            self._tts_consumer(cancel_check),
            name="streaming_tts_consumer"
        )

        try:
            # 等待两者完成
            await asyncio.gather(producer_task, consumer_task)
        except asyncio.CancelledError:
            logger.warning("[StreamingPipeline] 管线被取消")
        except Exception as e:
            logger.error(f"[StreamingPipeline] 管线异常: {e}")
        finally:
            # 确保任务清理
            if not producer_task.done():
                producer_task.cancel()
            if not consumer_task.done():
                consumer_task.cancel()

        logger.info(
            f"[StreamingPipeline] 管线完成, "
            f"句子数={len(self._audio_tasks)}, "
            f"全文长度={len(self._full_text)}"
        )

        return {
            "audio_tasks": self._audio_tasks,
            "sentence_counter": self._sentence_counter,
            "completed_audio_count": self._completed_count,
            "raw_llm_output": self._full_text,
            "streaming_processed": True,
        }

    async def _llm_producer(self, messages: list[dict], cancel_check):
        """
        LLM 生产者：流式接收 token，按句子边界切分并放入队列
        """
        from scheduler.session_manager import SessionManager
        session_manager = SessionManager.get_instance()

        adapter = DeepSeekR1Adapter()
        session_manager.register_adapter(adapter)

        buffer = ""
        token_count = 0

        try:
            logger.info(f"[StreamingPipeline] LLM 生产者启动, 消息数={len(messages)}")

            # 使用 aclosing 确保 async generator 正确关闭（避免 httpx 连接泄漏）
            async with aclosing(adapter.stream_chat_iterator(
                messages=messages,
                cancel_check=cancel_check,
            )) as token_iter:
                async for token in token_iter:
                    if cancel_check and cancel_check():
                        logger.warning("[StreamingPipeline] LLM 生产者收到打断信号")
                        break

                    buffer += token
                    self._full_text += token
                    token_count += 1

                    # 检查句子边界
                    while True:
                        sentence, buffer = self._extract_sentence(buffer)
                        if sentence:
                            logger.info(f"[StreamingPipeline] 句子就绪 #{self._sentence_counter}: {sentence[:40]}")
                            await self._sentence_queue.put(sentence)
                        else:
                            break

            # 流结束，刷新缓冲区剩余文本
            if buffer.strip():
                logger.info(f"[StreamingPipeline] 刷新缓冲区: {buffer[:40]}")
                await self._sentence_queue.put(buffer.strip())

            logger.info(f"[StreamingPipeline] LLM 生产者完成, token数={token_count}, 全文长度={len(self._full_text)}")

        except Exception as e:
            logger.error(f"[StreamingPipeline] LLM 生产者异常: {type(e).__name__}: {e}")
        finally:
            # 发送结束信号
            await self._sentence_queue.put(None)
            session_manager.unregister_adapter(adapter)
            await adapter.close()
            logger.info("[StreamingPipeline] LLM 生产者结束")

    async def _tts_consumer(self, cancel_check):
        """
        TTS 消费者：从队列取句子，逐句合成 TTS 并投递音频
        """
        logger.info("[StreamingPipeline] TTS 消费者启动")
        processed_count = 0

        try:
            while True:
                sentence = await self._sentence_queue.get()

                # 结束信号
                if sentence is None:
                    logger.info(f"[StreamingPipeline] TTS 消费者收到结束信号, 已处理句子数={processed_count}")
                    break

                # 打断检查
                from scheduler.session_manager import SessionManager
                if SessionManager.get_instance().is_interrupt_requested():
                    logger.warning("[StreamingPipeline] TTS 消费者收到打断信号，停止消费")
                    break

                # 处理句子
                logger.info(f"[StreamingPipeline] TTS 消费者开始处理句子 #{processed_count}: {sentence[:40]}")
                await self._process_sentence(sentence, cancel_check)
                processed_count += 1

        except Exception as e:
            logger.error(f"[StreamingPipeline] TTS 消费者异常: {type(e).__name__}: {e}")
        finally:
            logger.info(f"[StreamingPipeline] TTS 消费者结束, 已处理句子数={processed_count}")

    async def _process_sentence(self, text: str, cancel_check):
        """
        处理单个句子：文本预处理 + TTS 合成 + 音频投递
        """
        from scheduler.session_manager import SessionManager
        session_manager = SessionManager.get_instance()

        # 文本预处理
        tts_text = normalize_text_for_tts(text)
        tts_text = _strip_emotion_tags(tts_text)

        if not tts_text.strip():
            logger.warning("[StreamingPipeline] 句子预处理后为空，跳过")
            return

        # 创建 AudioTask
        task = AudioTask(
            sentence_index=self._sentence_counter,
            text=tts_text,
        )
        self._audio_tasks.append(task)
        self._sentence_counter += 1

        # TTS 合成
        adapter = create_tts_adapter()
        session_manager.register_adapter(adapter)

        try:
            # 应用克隆音色
            from web.routers.config_router import get_cloned_voice_id, get_tts_provider
            if get_tts_provider() == "volcengine":
                cloned_voice = get_cloned_voice_id()
                if cloned_voice:
                    adapter.set_cloned_voice(cloned_voice)

            # 分片间静音间隔（首块不加）
            if task.sentence_index > 0:
                await self._enqueue_block_gap(task.sentence_index)

            logger.info(
                f"[StreamingPipeline] 开始 TTS 合成句子 #{task.sentence_index}, "
                f"文本长度={len(tts_text)}"
            )

            # 合成音频
            audio_chunks = await adapter.synthesize(
                text=tts_text,
                cancel_check=cancel_check,
            )

            # 打断检查
            if session_manager.is_interrupt_requested():
                logger.warning(
                    f"[StreamingPipeline] 句子 #{task.sentence_index} 合成中被打断，跳过投递"
                )
                return

            # 标记完成
            task.audio_chunks = audio_chunks
            task.is_complete = True
            self._completed_count += 1

            # 投递音频
            from output.audio_queue import AudioQueueService
            audio_service = AudioQueueService.get_instance()
            for chunk in audio_chunks:
                await audio_service.enqueue(chunk, sentence_index=task.sentence_index)

            # 记录已播报文本（供连续追问时注入承接上下文）
            session_manager.track_spoken(task.text)
            # ⚠️ 弹幕回复绝不参与脚本断点追踪，不能调用 update_breakpoint！
            # 否则回复的句子序号（从0开始）会污染脚本播报的 breakpoint_idx，
            # 导致续接时从错误位置开始，重复播报已播过的脚本句子。

            logger.info(
                f"[StreamingPipeline] 句子 #{task.sentence_index} 合成完成, "
                f"音频分片数={len(audio_chunks)}"
            )

        except Exception as e:
            logger.error(f"[StreamingPipeline] TTS 合成异常: {e}")
        finally:
            session_manager.unregister_adapter(adapter)
            await adapter.close()

    def _extract_sentence(self, text: str) -> tuple:
        """
        从文本中提取一个完整句子
        返回 (sentence, remaining_text)
        """
        for i, ch in enumerate(text):
            if ch in _DELIMITER_CHARS:
                sentence = text[:i + 1].strip()
                remaining = text[i + 1:].strip()
                return sentence, remaining
        return None, text

    async def _enqueue_block_gap(self, sentence_index: int):
        """插入静音间隔"""
        vc = settings.volcengine
        if vc.block_gap <= 0 or vc.encoding != "pcm":
            return
        gap = vc.block_gap + random.uniform(-0.1, 0.2)
        gap = max(0.15, min(gap, 0.6))
        # 按采样点数生成再乘 2，保证偶数字节，避免 np.frombuffer(data, np.int16) 奇数字节报错
        num_samples = int(vc.sample_rate * gap)
        silence = b"\x00" * (num_samples * 2)
        from output.audio_queue import AudioQueueService
        await AudioQueueService.get_instance().enqueue(silence, sentence_index=sentence_index)
