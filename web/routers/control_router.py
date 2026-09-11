"""
直播控制路由 - 直播实例启停、模式切换、人工干预
"""

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from scheduler.event_bus import EventBus, EventType, LiveEvent
from scheduler.interrupt_controller import InterruptController
from scheduler.session_manager import SessionManager

router = APIRouter()


class StartRequest(BaseModel):
    """启动直播请求"""
    thread_id: str
    mode: str = "script_auto"  # script_auto / danmaku_reply


class ManualInputRequest(BaseModel):
    """手动下发口播话术"""
    text: str


class InterruptRequest(BaseModel):
    """手动打断请求"""
    reason: str = "manual_abort"


class LoopBroadcastRequest(BaseModel):
    """循环口播请求"""
    script: str  # 要循环播放的口播稿


@router.post("/start")
async def start_live(request: StartRequest):
    """启动直播实例"""
    logger.info(f"[control] 启动直播: thread_id={request.thread_id}, mode={request.mode}")

    session_manager = SessionManager.get_instance()
    event = LiveEvent(
        event_type=EventType.AUTO_BROADCAST if request.mode == "script_auto" else EventType.DANMAKU_MESSAGE,
        source="manual_start",
        content="",
        metadata={"mode": request.mode},
    )
    await session_manager.start_session(thread_id=request.thread_id, event=event)
    return {"status": "ok", "message": "直播已启动"}


@router.post("/stop")
async def stop_live():
    """停止直播实例"""
    logger.info("[control] 停止直播")

    session_manager = SessionManager.get_instance()
    await session_manager.abort()
    return {"status": "ok", "message": "直播已停止"}


@router.post("/interrupt")
async def interrupt_live(request: InterruptRequest):
    """手动打断当前播报"""
    logger.info(f"[control] 手动打断: reason={request.reason}")

    interrupt_controller = InterruptController.get_instance()
    event = LiveEvent(
        event_type=EventType.INTERRUPT,
        source="manual_interrupt",
        content="",
        metadata={"reason": request.reason},
    )
    await interrupt_controller.handle_interrupt(event)
    return {"status": "ok", "message": "已执行打断"}


@router.post("/manual_input")
async def manual_input(request: ManualInputRequest):
    """手动下发口播话术"""
    logger.info(f"[control] 手动下发话术: 长度={len(request.text)}")

    event_bus = EventBus.get_instance()
    event = LiveEvent(
        event_type=EventType.MANUAL_COMMAND,
        source="manual_input",
        content=request.text,
    )
    await event_bus.publish(event)
    return {"status": "ok", "message": "话术已下发"}


@router.post("/loop-start")
async def loop_start(request: LoopBroadcastRequest):
    """开始循环口播：手动触发，稿子直读并无限循环播放，直到手动停止"""
    if not request.script.strip():
        return {"status": "error", "message": "口播稿不能为空"}
    logger.info(f"[control] 开始循环口播: 稿子长度={len(request.script)}")

    session_manager = SessionManager.get_instance()
    # 开启循环标志：整稿播完后 _run_graph 会自动从头重播
    session_manager.start_loop_broadcast(request.script)
    # 触发第一遍（source=preview_broadcast → event_router 置 bypass_llm=True 稿子直读）
    await EventBus.get_instance().publish(LiveEvent(
        event_type=EventType.AUTO_BROADCAST,
        source="preview_broadcast",
        content=request.script,
    ))
    return {"status": "ok", "message": "已开始循环口播（稿子将无限循环，直到手动停止）"}


@router.post("/loop-stop")
async def loop_stop():
    """停止循环口播：关闭循环并中断当前播报"""
    logger.info("[control] 停止循环口播")

    session_manager = SessionManager.get_instance()
    # 先关循环标志（避免 abort 后 _run_graph 收尾又触发重播），再中断当前这一遍
    session_manager.stop_loop_broadcast()
    await session_manager.abort()
    return {"status": "ok", "message": "已停止循环口播"}


@router.get("/status")
async def get_status():
    """获取当前直播状态"""
    session = SessionManager.get_instance()
    return session.get_status()
