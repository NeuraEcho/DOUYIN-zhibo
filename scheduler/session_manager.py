"""
全局会话管理器 - 单直播间唯一实例
维护运行中的 LangGraph 会话，持有可取消的异步 Task
"""

import asyncio
from dataclasses import asdict
from typing import Optional

from loguru import logger

from graph.state import InterruptReason, LiveState, RunMode
from scheduler.event_bus import EventBus, EventType, LiveEvent

# 协作式打断宽限期（秒）：给 Graph 优雅收尾（跑完 round_complete 写入对话记忆）的时间，
# 超时则硬取消兜底。音频已在 abort 早期清空+淡出，故该宽限不影响止声速度。
COOPERATIVE_GRACE_SECONDS = 2.0

# 注入下一轮的续接上下文最大字符数，避免 Prompt 过长
MAX_INTERRUPTED_TEXT_CHARS = 160


class SessionManager:
    """
    全局会话管理器（单例）

    职责：
    - 维护运行中的 LangGraph 会话 ThreadID
    - 持有可取消的异步 Task（Graph 运行任务）
    - 提供抢占式中断接口：abort()
    - 管理会话生命周期
    """

    _instance: Optional["SessionManager"] = None

    def __init__(self):
        self._current_task: Optional[asyncio.Task] = None
        self._thread_id: Optional[str] = None
        self._is_busy: bool = False
        self._event_bus = EventBus.get_instance()
        self._compiled_graph = None
        # 持有当前活跃的适配器引用，用于 abort 时取消
        self._active_adapters: list = []
        # ===== 协作式打断信号（LiveTalking 式 interrupt_flag）=====
        self._interrupt_requested: bool = False              # 供节点/适配器 cancel_check 轮询
        self._interrupt_reason: Optional[InterruptReason] = None
        # ===== 话术续接：本轮已合成并投递播报的句子文本 =====
        self._spoken_texts: list = []
        # 待注入下一轮的续接上下文（abort 时捕获，_run_graph 时消费）
        self._pending_resume_reason: Optional[InterruptReason] = None
        self._pending_resume_text: str = ""
        # 最近一轮播报的完整文本（供前端轮询显示，弥补音频走声卡后浏览器看不到内容）
        self._last_round_text: str = ""
        # ===== 脚本分句断点追踪（精确续接核心）=====
        self._script_sentences: list = []    # 当前脚本的分句数组
        self._breakpoint_idx: int = 0        # 当前播报到第几句（断点索引）
        self._is_script_broadcast: bool = False  # 当前是否在播报脚本
        # ===== 循环口播模式（手动开启后，整稿播完自动从头再来）=====
        self._loop_broadcast: bool = False       # 是否开启无限循环口播
        self._loop_script: str = ""              # 循环播放的完整稿子
        self._loop_gap_seconds: float = 2.0      # 每遍之间的停顿秒数

    @classmethod
    def get_instance(cls) -> "SessionManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_compiled_graph(self, compiled_graph):
        """注入编译后的 LangGraph CompiledGraph"""
        self._compiled_graph = compiled_graph
        logger.info("[SessionManager] CompiledGraph 已注入")

    @property
    def is_busy(self) -> bool:
        return self._is_busy

    @property
    def is_script_broadcast(self) -> bool:
        """当前是否在播报脚本（SCRIPT_AUTO）。
        供 _event_loop 分流：脚本播报中弹幕走预就绪插入，问答态走即时打断。"""
        return self._is_script_broadcast

    @property
    def is_loop_broadcast(self) -> bool:
        """是否处于循环口播模式。
        供 llm_stream 决定稿子直读遍数：循环口播=1 遍（循环由调度驱动），试播=10 遍。"""
        return self._loop_broadcast

    @property
    def thread_id(self) -> Optional[str]:
        return self._thread_id

    async def start_session(self, thread_id: str, event: LiveEvent):
        """
        启动新的 LangGraph 会话

        :param thread_id: 直播间唯一会话ID
        :param event: 触发事件
        """
        if self._is_busy:
            logger.warning("[SessionManager] 会话忙碌，先执行中断再启动新会话")
            # 新内容抢占 → 标记为弹幕打断，捕获续接上下文供下一轮衔接
            await self.abort(reason=InterruptReason.DANMAKU_INTERRUPT)

        self._thread_id = thread_id
        self._is_busy = True

        logger.info(f"[SessionManager] 启动会话: thread_id={thread_id}, event={event.event_type.value}")

        # 创建并启动 LangGraph 运行任务
        self._current_task = asyncio.create_task(
            self._run_graph(event),
            name=f"graph_session_{thread_id}",
        )

    async def abort(self, reason: InterruptReason = InterruptReason.MANUAL_ABORT):
        """
        抢占式中断（协作式优先 + 硬取消兜底）- 执行一套原子动作：
        ① 置协作式打断标志：让正在运行的 LLM/TTS adapter 通过 cancel_check 优雅收尾，
           保留已生成内容并跑完 round_complete 写入对话记忆
        ② 立即清空音频队列并对尾音淡出：让正在播报的声音快速且平缓地停下
        ③ 通知网关取消正在请求的 DeepSeek / TTS 云端流
        ④ 宽限期内等待 Graph 协作式收尾，超时则硬取消兜底
        ⑤ 捕获被打断时已播报的话术，供下一轮语义续接
        ⑥ 将会话重置为空闲状态
        """
        logger.warning(f"[SessionManager] 执行抢占式中断 abort(reason={reason.value})")

        # ① 置协作式打断标志（此后 adapter 的 cancel_check 会读到 True 并优雅收尾）
        self.request_interrupt(reason)

        # ② 设置音频播放器“播完当前句子后停止”标志（不在句子中间截断，听感更自然）
        from output.audio_queue import AudioQueueService
        from output.audio_player import AudioPlayer
        audio_service = AudioQueueService.get_instance()
        player = AudioPlayer.get_instance()
        player.set_finish_sentence_then_stop()
        # 清空软件队列（丢弃未投递的分片）
        await audio_service.clear()
        # 放入哨兵值唤醒阻塞在 dequeue 的 AudioPlayer，让它检查停止标志
        await audio_service._queue.put(None)

        # ③ 通知适配器取消云端请求（协议级优雅取消）
        for adapter in self._active_adapters:
            try:
                await adapter.cancel()
            except Exception as e:
                logger.error(f"[SessionManager] 适配器取消异常: {e}")
        self._active_adapters.clear()

        # ④ 宽限期内等待 Graph 协作式收尾，超时则硬取消兜底
        if self._current_task and not self._current_task.done():
            try:
                await asyncio.wait_for(self._current_task, timeout=COOPERATIVE_GRACE_SECONDS)
                logger.info("[SessionManager] Graph 已协作式收尾")
            except asyncio.TimeoutError:
                logger.warning("[SessionManager] 协作式收尾超时，执行硬取消兜底")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[SessionManager] 等待 Graph 收尾异常: {e}")
            if self._current_task and not self._current_task.done():
                self._current_task.cancel()
                try:
                    await self._current_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            self._current_task = None

        # 收尾期间可能残留少量已入队音频，二次清空
        await audio_service.clear()
        # 协作式句子边界停止已让播放器自然停下；若超时未停，硬停兆底
        if player._finish_sentence_then_stop:
            if player._virtual_sound_card:
                player._virtual_sound_card.stop()
            player._finish_sentence_then_stop = False
            logger.info("[SessionManager] 协作式句子边界停止已生效，waveOutReset 兆底")

        # ⑤ 捕获续接上下文（仅「新内容抢占」场景需要续接；人工停止不续接）
        if reason == InterruptReason.DANMAKU_INTERRUPT:
            self._capture_interrupted_text(reason)

        # ⑥ 重置会话状态（保留 _pending_resume_* 供下一轮注入）
        self._is_busy = False
        self._thread_id = None
        # 清理可能残留的问答预生成（手动停止/会话切换后不应再冒出过期回答）
        from scheduler.qa_inserter import QAInserter
        QAInserter.get_instance().discard()
        logger.info("[SessionManager] 会话已重置为空闲状态")

    async def _run_graph(self, event: LiveEvent):
        """
        运行 LangGraph 完整节点链

        流程：
        1. 构建初始 LiveState
        2. 调用 compiled_graph.ainvoke() 运行完整 Graph
        3. 处理 human_review interrupt（Command resume）
        4. 异常捕获与状态清理
        """
        if not self._compiled_graph:
            logger.error("[SessionManager] CompiledGraph 未注入，无法运行")
            self._is_busy = False
            return

        from config.settings import settings

        # 新一轮开始：清理上一轮打断标志（否则会误杀本轮新回复），取出待注入的续接上下文
        self.clear_interrupt()
        self.reset_spoken()
        resume_reason = self._pending_resume_reason or InterruptReason.NONE
        resume_text = self._pending_resume_text or ""
        self._pending_resume_reason = None
        self._pending_resume_text = ""
        if resume_reason != InterruptReason.NONE and resume_text:
            logger.info(
                f"[SessionManager] 注入续接上下文: reason={resume_reason.value}, "
                f"text='{resume_text[:40]}…'"
            )

        # 构建初始状态
        initial_state = {
            "thread_id": self._thread_id,
            "run_mode": RunMode.IDLE,
            "system_prompt": settings.default_system_prompt,
            "trigger_source": event.source,
            "trigger_content": event.content,
            "trigger_nickname": (event.metadata or {}).get("user_name", ""),
            "context_window_size": settings.langgraph.context_window_size,
            # 打断续接：把上一轮被打断的话术作为上下文注入，激活 prompt_assembler 衔接分支
            "interrupt_reason": resume_reason,
            "interrupted_text": resume_text,
        }

        config = {
            "configurable": {
                "thread_id": self._thread_id,
            }
        }

        try:
            logger.info(f"[SessionManager] Graph 开始运行: thread_id={self._thread_id}")

            # 运行 Graph
            result = await self._compiled_graph.ainvoke(initial_state, config=config)

            # 记录本轮播报文本，供前端轮询显示（直读=稿子，弹幕=AI回复含承接语）
            if isinstance(result, dict):
                self._last_round_text = result.get("raw_llm_output", "") or ""

            logger.info("[SessionManager] Graph 运行完成")

            # 脚本播报收尾：有未播句子则续接剩余；整稿播完且循环模式开启则从头再来
            if self._is_script_broadcast and not self._interrupt_requested:
                if self._breakpoint_idx < len(self._script_sentences):
                    # 还有未播句子（弹幕打断后续接场景）
                    self._schedule_resume_session()
                elif self._loop_broadcast:
                    # 整稿已播完 + 循环口播开启 → 从头重播
                    self._schedule_loop_session()

        except asyncio.CancelledError:
            logger.warning("[SessionManager] Graph 任务被取消")
        except Exception as e:
            logger.error(f"[SessionManager] Graph 运行异常: {e}")
        finally:
            self._is_busy = False
            self._current_task = None

    def register_adapter(self, adapter):
        """注册活跃适配器，用于 abort 时取消"""
        self._active_adapters.append(adapter)

    def unregister_adapter(self, adapter):
        """注销活跃适配器"""
        if adapter in self._active_adapters:
            self._active_adapters.remove(adapter)

    # ========== 协作式打断信号（LiveTalking 式 interrupt_flag）==========
    def request_interrupt(self, reason: InterruptReason):
        """置打断标志——正在运行的 adapter.cancel_check() 会读到并优雅收尾"""
        self._interrupt_requested = True
        self._interrupt_reason = reason

    def is_interrupt_requested(self) -> bool:
        """节点/适配器轮询：是否需要优雅中断"""
        return self._interrupt_requested

    def clear_interrupt(self):
        """新一轮会话开始前清理打断标志，避免误杀新回复"""
        self._interrupt_requested = False
        self._interrupt_reason = None

    # ========== 话术续接追踪 ==========
    def track_spoken(self, text: str):
        """记录本轮已合成并投递播报的句子（供打断时捕获续接上下文）"""
        if text:
            self._spoken_texts.append(text)

    def reset_spoken(self):
        """新一轮会话开始前清空已播报追踪（不清除脚本断点，跨会话保留）"""
        self._spoken_texts = []

    # ========== 脚本分句断点追踪（精确续接核心）==========
    def set_script_sentences(self, sentences: list):
        """设置脚本分句数组（试播/播报开始时调用）"""
        self._script_sentences = sentences
        self._breakpoint_idx = 0
        self._is_script_broadcast = True
        logger.info(
            f"[SessionManager] 脚本分句已注册: {len(sentences)} 句, "
            f"breakpoint_idx=0"
        )

    def update_breakpoint(self, sentence_idx: int):
        """TTS 每播完一句后更新断点索引"""
        self._breakpoint_idx = sentence_idx + 1
        logger.debug(
            f"[SessionManager] 断点更新: sentence_idx={sentence_idx}, "
            f"breakpoint_idx={self._breakpoint_idx}/{len(self._script_sentences)}"
        )

    def get_remaining_script(self) -> str:
        """获取剩余未播脚本文本"""
        if not self._script_sentences or self._breakpoint_idx >= len(self._script_sentences):
            return ""
        remaining = self._script_sentences[self._breakpoint_idx:]
        return "\n".join(remaining)

    def clear_script_breakpoint(self):
        """清除脚本断点（脚本全部播完或手动停止时调用）"""
        self._script_sentences = []
        self._breakpoint_idx = 0
        self._is_script_broadcast = False
        # 脚本结束，一并清理未插入的问答预生成
        from scheduler.qa_inserter import QAInserter
        QAInserter.get_instance().discard()
        logger.info("[SessionManager] 脚本断点已清除")

    def _capture_interrupted_text(self, reason: InterruptReason):
        """捕获被打断时已播报的话术，存入待注入的续接上下文"""
        if not self._spoken_texts:
            self._pending_resume_reason = None
            self._pending_resume_text = ""
            return
        joined = " ".join(self._spoken_texts).strip()
        if len(joined) > MAX_INTERRUPTED_TEXT_CHARS:
            joined = "…" + joined[-MAX_INTERRUPTED_TEXT_CHARS:]
        self._pending_resume_reason = reason
        self._pending_resume_text = joined
        logger.info(
            f"[SessionManager] 捕获续接上下文: reason={reason.value}, "
            f"len={len(joined)}, text='{joined[:50]}…'"
        )

    def _schedule_resume_session(self):
        """
        弹幕回复完成后，自动调度续接播报会话。
        使用脚本断点追踪获取剩余未播脚本，精确续接。
        延迟 1 秒执行，避免与当前会话收尾冲突。
        """
        async def _delayed_resume():
            try:
                await asyncio.sleep(1.0)  # 等待当前会话 fully 清理
                remaining = self.get_remaining_script()
                if not remaining:
                    logger.info("[SessionManager] 脚本已全部播完，无需续接")
                    self.clear_script_breakpoint()
                    return
                logger.info(
                    f"[SessionManager] 自动调度续接播报会话, "
                    f"breakpoint_idx={self._breakpoint_idx}, "
                    f"剩余脚本长度={len(remaining)}"
                )
                resume_event = LiveEvent(
                    event_type=EventType.AUTO_BROADCAST,
                    source="script_resume",  # 新来源标识，走稿子直读
                    content=remaining,
                )
                await self.start_session(self._thread_id or "resume", resume_event)
            except Exception as e:
                logger.error(f"[SessionManager] 续接播报调度失败: {e}")

        asyncio.create_task(_delayed_resume(), name="resume_broadcast")

    def start_loop_broadcast(self, script: str):
        """开启循环口播：记录稿子，整稿播完后自动从头再来（直到 stop_loop_broadcast）"""
        self._loop_broadcast = True
        self._loop_script = script
        logger.info(
            f"[SessionManager] 循环口播已开启, 稿子长度={len(script)}, "
            f"间隔={self._loop_gap_seconds}秒"
        )

    def stop_loop_broadcast(self):
        """停止循环口播（关闭循环标志；当前这一遍是否立即中断由调用方决定）"""
        self._loop_broadcast = False
        self._loop_script = ""
        logger.info("[SessionManager] 循环口播已停止")

    def set_loop_gap(self, seconds: float):
        """设置循环口播每遍之间的停顿秒数"""
        self._loop_gap_seconds = max(0.0, float(seconds))
        logger.info(f"[SessionManager] 循环口播间隔已更新: {self._loop_gap_seconds}秒")

    def _schedule_loop_session(self):
        """
        循环口播：整稿播完一遍后，延迟片刻从头重新播报。
        复用稿子直读（source=preview_broadcast）：新会话首次分句会重新注册
        脚本句子并把断点归零，从而实现无缝循环。
        """
        async def _delayed_loop():
            try:
                await asyncio.sleep(self._loop_gap_seconds)
                if not self._loop_broadcast:
                    logger.info("[SessionManager] 循环口播已关闭，停止重播")
                    self.clear_script_breakpoint()
                    return
                logger.info("[SessionManager] 循环口播：整稿已播完，从头重播")
                loop_event = LiveEvent(
                    event_type=EventType.AUTO_BROADCAST,
                    source="preview_broadcast",  # 稿子直读，bypass LLM
                    content=self._loop_script,
                )
                await self.start_session(self._thread_id or "loop", loop_event)
            except Exception as e:
                logger.error(f"[SessionManager] 循环口播调度失败: {e}")

        asyncio.create_task(_delayed_loop(), name="loop_broadcast")

    def get_status(self) -> dict:
        """获取当前会话状态"""
        return {
            "thread_id": self._thread_id,
            "is_busy": self._is_busy,
            "task_running": self._current_task is not None and not self._current_task.done(),
            "last_text": self._last_round_text,
        }
