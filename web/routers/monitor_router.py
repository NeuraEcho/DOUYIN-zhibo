"""
实时监控路由 - 会话状态、对话日志、接口调用耗时、异常告警
"""

from fastapi import APIRouter
from loguru import logger

router = APIRouter()


@router.get("/status")
async def get_session_status():
    """获取当前会话状态"""
    from scheduler.session_manager import SessionManager
    session = SessionManager.get_instance()
    return session.get_status()


@router.get("/audio-queue")
async def get_audio_queue_status():
    """获取音频队列状态"""
    from output.audio_queue import AudioQueueService
    queue = AudioQueueService.get_instance()
    return {
        "size": queue.size,
        "is_empty": queue.is_empty,
    }


@router.get("/logs")
async def get_recent_logs(limit: int = 50):
    """获取最近日志（简化版）"""
    # TODO: 实现日志读取（从 loguru 的 sink 或文件读取）
    return {"logs": [], "message": "日志功能待实现"}


@router.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "service": "AI 云端口播直播系统",
    }


@router.get("/usage")
async def get_usage():
    """获取实时用量与成本统计（TTS 字符 / LLM token / 估算费用），供前端成本看板轮询"""
    from scheduler.usage_tracker import UsageTracker
    return UsageTracker.get_instance().get_usage()


@router.post("/usage/reset")
async def reset_usage():
    """清零用量与成本统计（重新开始累计）"""
    from scheduler.usage_tracker import UsageTracker
    UsageTracker.get_instance().reset()
    return {
        "status": "ok",
        "message": "用量统计已清零",
        "data": UsageTracker.get_instance().get_usage(),
    }
