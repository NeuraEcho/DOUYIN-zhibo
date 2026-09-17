"""
LangGraph StateGraph 完整状态定义
直播主播 Agent 的核心状态对象，贯穿所有节点
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class RunMode(str, Enum):
    """运行模式"""
    IDLE = "idle"                    # 空闲等待
    SCRIPT_AUTO = "script_auto"      # 自动脚本轮播
    DANMAKU_REPLY = "danmaku_reply"  # 弹幕互动回复
    MANUAL_INPUT = "manual_input"    # 人工手动下发


class InterruptReason(str, Enum):
    """打断原因标记，用于上下文衔接"""
    NONE = "none"
    DANMAKU_INTERRUPT = "danmaku_interrupt"
    MANUAL_ABORT = "manual_abort"


@dataclass
class AudioTask:
    """单个句子的音频合成任务"""
    sentence_index: int                              # 句子序号
    text: str                                        # 待合成文本
    audio_chunks: list[bytes] = field(default_factory=list)  # 合成的音频分片
    is_complete: bool = False                        # 是否合成完成


@dataclass
class LiveState:
    """
    LangGraph StateGraph 的完整状态对象

    贯穿所有节点，承载：
    - 会话标识与运行模式
    - 对话上下文记忆
    - 流式文本处理缓冲
    - 音频任务追踪
    - 打断与中断状态
    - 事件触发输入
    - 人工审核状态
    - 运行控制标志
    """

    # ========== 会话标识 ==========
    thread_id: str                                  # 直播间唯一会话ID
    run_mode: RunMode = RunMode.IDLE                # 当前运行模式

    # ========== 对话上下文（LangGraph add_messages reducer） ==========
    messages: Annotated[list[BaseMessage], add_messages] = field(default_factory=list)

    # ========== 主播人设 ==========
    system_prompt: str = ""                         # 主播人设 Prompt

    # ========== 流式文本处理 ==========
    raw_llm_output: str = ""                        # LLM 原始流式输出累积
    text_segment_buffer: str = ""                   # 分句临时缓冲（未成句的 token 暂存）
    current_text_chunk: str = ""                    # 当前已切割待合成语音的完整短句
    sentence_counter: int = 0                       # 句子序号计数器

    # ========== 音频任务追踪 ==========
    audio_tasks: list[AudioTask] = field(default_factory=list)
    completed_audio_count: int = 0

    # ========== 打断与中断 ==========
    interrupt_reason: InterruptReason = InterruptReason.NONE
    interrupted_text: str = ""                      # 被打断时正在播报的文本

    # ========== 事件输入 ==========
    trigger_source: str = ""                        # 触发来源: "timer" / "danmaku" / "channels"（视频号） / "manual" / "preview_broadcast" / "preview_danmaku" / "script_resume"
    trigger_content: str = ""                       # 触发内容
    trigger_nickname: str = ""                      # 提问观众昵称（弹幕/模拟提问），回答时格式化为「xx姐姐」称呼

    # ========== 直读模式（试播：稿子原样播报，旁路 LLM）==========
    bypass_llm: bool = False

    # ========== 流式管线已处理标记 ==========
    streaming_processed: bool = False

    # ========== 人工审核 ==========
    is_need_human_review: bool = False
    human_review_result: Optional[bool] = None      # True=通过, False=拒绝, None=待审

    # ========== 运行控制 ==========
    is_round_complete: bool = False
    error_message: Optional[str] = None

    # ========== 配置参数（运行时可调） ==========
    context_window_size: int = 10
    max_retry_count: int = 3
