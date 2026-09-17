"""
问答插入协调器 - 「预就绪 · 低优先级 · 伺机句尾插入」核心组件

设计要点（对齐最终版设计方案）：
- 脚本播报中收到提问弹幕：不打断、不 abort、不新建会话，
  由本协调器在后台独立异步通道静默预生成「完整可播放回答音频」。
- 回答音频 100% 就绪后才激活「可插入状态」（READY）。
- 脚本 TTS 循环在句尾门控处调用 try_consume_for_insertion()，
  在弹性窗口（max_skip_sentences）内取出回答音频，由脚本循环统一 enqueue。
- 协调器本身绝不 enqueue 音频、绝不触碰脚本断点（避免排序冲突与断点污染）。

四态流转：IDLE → GENERATING → READY →（脚本循环取走播放）→ IDLE
防堆积：同一时刻仅一条预生成；新弹幕覆盖旧；超时/失败静默丢弃。
"""

import asyncio
from enum import Enum
from typing import Optional

from loguru import logger

from config.settings import settings

# 中英文标点断句字符集合（与 sentence_splitter / streaming_pipeline 保持一致）
_DELIMITER_CHARS = set('。！？；\n.!?;')


class QAState(str, Enum):
    """问答预生成状态机"""
    IDLE = "idle"              # 无任务空闲态
    GENERATING = "generating"  # 问答生成中态（无插入权限）
    READY = "ready"            # 问答就绪待插入态（拥有句尾插入权限）


def _split_sentences(text: str) -> list[str]:
    """按中英文标点断句，返回完整句子列表（末尾无标点的残句也保留）"""
    sentences = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _DELIMITER_CHARS:
            sent = text[start:i + 1].strip()
            if sent:
                sentences.append(sent)
            start = i + 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


class QAInserter:
    """
    问答插入协调器（单例）

    职责：
    - start_pregeneration(question, nickname)：后台静默预生成完整回答音频（回答带观众昵称称呼）
    - is_ready() / try_consume_for_insertion(idx)：就绪门控 + 弹性窗口
    - discard()：取消预生成、清空就绪回答（覆盖/停止/超时时调用）
    """

    _instance: Optional["QAInserter"] = None

    def __init__(self):
        self._state: QAState = QAState.IDLE
        self._question: str = ""
        self._nickname: str = ""
        self._answer_chunks: list[bytes] = []
        # 回答就绪时刻对应的脚本句索引（用于弹性窗口计算）
        self._ready_at_script_index: int = 0
        self._task: Optional[asyncio.Task] = None
        # 代次令牌：每次 start/discard 自增，旧任务据此自我终止
        self._generation: int = 0

    @classmethod
    def get_instance(cls) -> "QAInserter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def state(self) -> QAState:
        return self._state

    def is_ready(self) -> bool:
        """回答音频是否已 100% 就绪、拥有插入资格"""
        return self._state == QAState.READY and bool(self._answer_chunks)

    def start_pregeneration(self, question: str, nickname: str = ""):
        """
        启动后台静默预生成。
        若已有 GENERATING/READY 任务，先 discard（新弹幕覆盖旧，始终响应最新问题）。
        :param nickname: 提问观众昵称，回答时会格式化为「xx姐姐」称呼
        """
        if not settings.qa_insertion.enabled:
            logger.debug("[QAInserter] 插入机制未启用，忽略预生成请求")
            return
        if not question or not question.strip():
            return

        # 覆盖旧任务
        self.discard()

        self._question = question.strip()
        self._nickname = nickname or ""
        self._state = QAState.GENERATING
        self._generation += 1
        gen = self._generation

        self._task = asyncio.create_task(
            self._pregenerate(self._question, gen, self._nickname),
            name="qa_pregeneration",
        )
        logger.info(f"[QAInserter] 启动后台预生成 (gen={gen}): {self._question[:40]}")

    async def _pregenerate(self, question: str, gen: int, nickname: str = ""):
        """
        后台独立异步通道：LLM 问答生成 + TTS 音频合成，产出完整可播放回答语音。
        全程静默——不 enqueue、不打断脚本。
        """
        from adapters.deepseek_adapter import DeepSeekR1Adapter
        from graph.nodes.prompt_assembler import QA_SYSTEM_PROMPT
        from common.nickname_formatter import format_viewer_nickname
        from services.tts_factory import create_tts_adapter
        from services.text_enricher_service import normalize_text_for_tts
        from graph.nodes.tts_synthesize import _strip_emotion_tags

        cfg = settings.qa_insertion

        def _cancelled() -> bool:
            # 代次变化（被新弹幕覆盖或 discard）即视为取消
            return gen != self._generation

        llm_adapter = DeepSeekR1Adapter()
        tts_adapter = None
        try:
            # 1) LLM 生成完整回答文本（带总超时）
            # 带昵称回答：注入提问观众的格式化称呼，让 AI 先自然地叫一声「xx姐姐」
            call_name = format_viewer_nickname(nickname)
            # 商品问答知识库：注入商品信息 + 参考示例（仅问答链路）
            from scheduler.product_knowledge import ProductKnowledge
            product_ctx = ProductKnowledge.get_instance().build_qa_context()
            messages = [{"role": "system", "content": QA_SYSTEM_PROMPT}]
            if product_ctx:
                messages.append({"role": "system", "content": product_ctx})
            messages.append({"role": "system", "content": (
                f"这位提问观众的称呼是「{call_name}」。请在回答开头自然地叫一声「{call_name}」，"
                f"然后简洁回答问题本身；只称呼一次，不要加欢迎/引导打字等播报话术。"
            )})
            messages.append({"role": "user", "content": question})
            full_text = await asyncio.wait_for(
                llm_adapter.stream_chat(messages=messages, cancel_check=_cancelled),
                timeout=cfg.pregen_timeout_seconds,
            )
            if _cancelled():
                logger.info(f"[QAInserter] 预生成被覆盖/取消 (gen={gen})，丢弃")
                return
            if not full_text or not full_text.strip():
                logger.warning(f"[QAInserter] LLM 返回空回答 (gen={gen})，丢弃")
                self._reset_to_idle()
                return

            # 2) 分句 → 逐句 TTS 合成，收集完整音频分片
            sentences = _split_sentences(full_text)
            tts_adapter = create_tts_adapter()
            # 应用运行时克隆音色（与 tts_synthesize.py 一致：火山引擎 / ElevenLabs）
            from web.routers.config_router import get_cloned_voice_id, get_tts_provider
            if get_tts_provider() in ("volcengine", "elevenlabs"):
                cloned = get_cloned_voice_id()
                if cloned:
                    tts_adapter.set_cloned_voice(cloned)

            chunks: list[bytes] = []
            for sent in sentences:
                if _cancelled():
                    logger.info(f"[QAInserter] 合成中被覆盖/取消 (gen={gen})，丢弃")
                    return
                tts_text = _strip_emotion_tags(normalize_text_for_tts(sent))
                if not tts_text.strip():
                    continue
                audio = await asyncio.wait_for(
                    tts_adapter.synthesize(text=tts_text, cancel_check=_cancelled),
                    timeout=cfg.pregen_timeout_seconds,
                )
                chunks.extend(audio)

            if _cancelled():
                return
            if not chunks:
                logger.warning(f"[QAInserter] 回答音频为空 (gen={gen})，丢弃")
                self._reset_to_idle()
                return

            # 3) 就绪：记录回答音频 + 就绪时的脚本句索引，激活插入资格
            from output.audio_player import AudioPlayer
            self._answer_chunks = chunks
            self._ready_at_script_index = AudioPlayer.get_instance().current_sentence_index
            self._state = QAState.READY
            logger.info(
                f"[QAInserter] 预生成完成 → READY (gen={gen}), "
                f"分片数={len(chunks)}, ready_at_script_index={self._ready_at_script_index}"
            )

        except asyncio.TimeoutError:
            logger.warning(f"[QAInserter] 预生成超时 (gen={gen}, {cfg.pregen_timeout_seconds}s)，静默丢弃")
            self._reset_to_idle()
        except asyncio.CancelledError:
            logger.info(f"[QAInserter] 预生成任务被取消 (gen={gen})")
            raise
        except Exception as e:
            logger.error(f"[QAInserter] 预生成异常 (gen={gen}): {type(e).__name__}: {e}，静默丢弃")
            self._reset_to_idle()
        finally:
            await llm_adapter.close()
            if tts_adapter is not None:
                await tts_adapter.close()

    def try_consume_for_insertion(self, current_script_index: int) -> Optional[list[bytes]]:
        """
        脚本 TTS 循环在句尾门控处调用：
        - READY 且在弹性窗口内（跳过句数 <= max_skip_sentences）→ 返回回答音频并置 IDLE
        - READY 但超过窗口上限 → 作废丢弃，返回 None
        - 其他（IDLE/GENERATING）→ 返回 None（生成中态绝无插入权限）
        """
        if self._state != QAState.READY or not self._answer_chunks:
            return None

        cfg = settings.qa_insertion
        skipped = current_script_index - self._ready_at_script_index
        if skipped > cfg.max_skip_sentences:
            logger.warning(
                f"[QAInserter] 超过弹性窗口（跳过 {skipped} > {cfg.max_skip_sentences} 句），"
                f"作废本次回答"
            )
            self.discard()
            return None

        chunks = self._answer_chunks
        logger.info(
            f"[QAInserter] 句尾插入回答 (script_idx={current_script_index}, "
            f"ready_at={self._ready_at_script_index}, 分片数={len(chunks)})"
        )
        self._reset_to_idle()
        return chunks

    def flush_if_ready(self) -> Optional[list[bytes]]:
        """脚本播完时的收尾兜底：若仍有就绪回答未插入，取出播放，避免临近结尾的提问被丢弃"""
        if self._state == QAState.READY and self._answer_chunks:
            chunks = self._answer_chunks
            logger.info(f"[QAInserter] 脚本收尾兜底插入回答, 分片数={len(chunks)}")
            self._reset_to_idle()
            return chunks
        return None

    def discard(self):
        """取消后台预生成 + 清空就绪回答，回到 IDLE"""
        self._generation += 1  # 令正在运行的旧任务自我终止
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._reset_to_idle()

    def _reset_to_idle(self):
        self._state = QAState.IDLE
        self._answer_chunks = []
        self._question = ""
        self._nickname = ""
        self._ready_at_script_index = 0
