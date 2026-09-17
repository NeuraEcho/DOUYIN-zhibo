"""
AI 云端口播直播系统 - 主入口
启动所有服务协程：事件采集、LangGraph 编排、音频播放、Web API
"""

import asyncio

import uvicorn
from loguru import logger

from config.settings import settings


async def main():
    """主启动函数"""
    logger.info("=" * 60)
    logger.info("AI 云端口播直播系统 启动中...")
    logger.info("=" * 60)

    # 初始化日志
    from common.logger import setup_logger
    setup_logger()

    # 初始化各服务
    from scheduler.collectors.danmaku_collector import DanmakuCollector
    from scheduler.collectors.timer_collector import TimerCollector
    from scheduler.event_bus import EventBus
    from scheduler.session_manager import SessionManager
    from scheduler.interrupt_controller import InterruptController
    from output.audio_player import AudioPlayer
    from output.virtual_sound_card import VirtualSoundCard

    # 初始化单例
    event_bus = EventBus.get_instance()
    session_manager = SessionManager.get_instance()
    interrupt_controller = InterruptController.get_instance()
    audio_player = AudioPlayer.get_instance()
    virtual_sound_card = VirtualSoundCard()

    # 初始化虚拟声卡
    if not virtual_sound_card.initialize():
        logger.warning("虚拟声卡初始化失败，音频输出可能不可用")

    # 注入虚拟声卡到音频播放器
    audio_player.set_virtual_sound_card(virtual_sound_card)

    # 构建并编译 LangGraph
    from graph.builder import build_live_graph
    from langgraph.checkpoint.memory import MemorySaver

    state_graph = build_live_graph()
    checkpointer = MemorySaver()
    compiled_graph = state_graph.compile(checkpointer=checkpointer)
    logger.info("LangGraph StateGraph 编译完成（MemorySaver checkpointer）")

    # 将编译后的 Graph 注入 SessionManager
    session_manager.set_compiled_graph(compiled_graph)

    # 创建所有协程任务
    tasks = [
        # 弹幕监听（单例：前端改直播间号后可触发同一实例 reconnect 用新房间号重连）
        asyncio.create_task(DanmakuCollector.get_instance().start(), name="danmaku_collector"),
        # 定时任务（已禁用：不再自动定时播报，改为 Web 后台手动触发的「循环口播」）
        # 如需恢复定时播报，取消下面这行注释即可
        # asyncio.create_task(TimerCollector().start(), name="timer_collector"),
        # 音频播放
        asyncio.create_task(audio_player.start(), name="audio_player"),
        # 事件消费循环
        asyncio.create_task(_event_loop(event_bus, session_manager, interrupt_controller), name="event_loop"),
    ]

    logger.info(f"所有服务协程已启动, 共 {len(tasks)} 个任务")
    # 打印可点击的访问地址：0.0.0.0/:: 是「监听所有网卡」的绑定地址，不能作为浏览器访问目标
    # （Windows 上照着 0.0.0.0 点会打不开），故本机访问统一显示 127.0.0.1，并附带局域网访问提示。
    _host, _port = settings.web.host, settings.web.port
    if _host in ("0.0.0.0", "::", ""):
        logger.info(f"Web 管理后台（本机访问）: http://127.0.0.1:{_port}")
        try:
            import socket
            _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _s.settimeout(0.5)
            _s.connect(("8.8.8.8", 80))  # UDP connect 不会真正发包，仅用于拿到出口网卡内网 IP
            _lan_ip = _s.getsockname()[0]
            _s.close()
        except Exception:
            _lan_ip = ""
        if _lan_ip:
            logger.info(f"Web 管理后台（局域网其他设备）: http://{_lan_ip}:{_port}")
        else:
            logger.info(f"Web 管理后台（局域网其他设备）: http://<本机内网IP>:{_port}（用 ipconfig 查 IPv4 地址）")
    else:
        logger.info(f"Web 管理后台: http://{_host}:{_port}")

    # 启动 Web 服务（阻塞主线程）
    config = uvicorn.Config(
        app="web.app:create_app",
        factory=True,
        host=settings.web.host,
        port=settings.web.port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在关闭...")
    finally:
        # 清理所有任务
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        virtual_sound_card.close()
        logger.info("系统已安全关闭")


async def _event_loop(event_bus, session_manager, interrupt_controller):
    """
    事件消费主循环
    从事件总线持续消费事件，分发给会话管理器或中断控制器
    弹幕事件先经过 DanmakuFilter 过滤，只有命中关键词的弹幕才触发打断
    """
    from scheduler.event_bus import EventType
    from scheduler.danmaku_filter import DanmakuFilter
    from web.routers.config_router import get_room_id

    danmaku_filter = DanmakuFilter.get_instance()
    logger.info("[event_loop] 事件消费循环启动")

    while True:
        try:
            event = await event_bus.consume()

            if event.event_type == EventType.INTERRUPT:
                # 打断事件 → 中断控制器
                await interrupt_controller.handle_interrupt(event)

            elif event.event_type == EventType.DANMAKU_MESSAGE:
                # 模拟提问（前端手动测试）直接放行，不受关键词/冷却过滤；真实弹幕才过滤
                if event.source != "preview_danmaku" and not danmaku_filter.should_interrupt(event.content):
                    logger.debug(f"[event_loop] 弹幕未命中打断条件，忽略: {event.content}")
                    continue

                # 提问原文（去掉 [用户名]: 前缀）+ 提问观众昵称，供问答生成使用
                question = event.content
                nickname = ""
                if event.metadata:
                    question = event.metadata.get("raw_content", event.content)
                    nickname = event.metadata.get("user_name", "")

                if (session_manager.is_busy
                        and session_manager.is_script_broadcast
                        and settings.qa_insertion.enabled):
                    # 脚本播报中 → 预就绪伺机插入：不打断、不新建会话，
                    # 后台静默预生成回答音频，就绪后由脚本 TTS 循环在句尾插入
                    from scheduler.qa_inserter import QAInserter
                    QAInserter.get_instance().start_pregeneration(question, nickname)
                    logger.info(f"[event_loop] 脚本播报中，转预就绪插入通道: {question}")
                else:
                    # 问答进行中（DANMAKU_REPLY）或空闲 → 即时打断/即时回答（保留原有行为）
                    await session_manager.start_session(
                        thread_id=get_room_id() or "default",
                        event=event,
                    )

            else:
                # 普通事件（定时播报、手动下发等）→ 会话管理器启动新会话
                await session_manager.start_session(
                    thread_id=get_room_id() or "default",
                    event=event,
                )

        except asyncio.CancelledError:
            logger.info("[event_loop] 事件消费循环被取消")
            break
        except Exception as e:
            logger.error(f"[event_loop] 事件处理异常: {e}")


if __name__ == "__main__":
    asyncio.run(main())
