"""
异常处理节点 - 异常降级处理（简化版，后续完善）
"""

from loguru import logger

from graph.state import LiveState, RunMode


def error_handler(state: LiveState) -> dict:
    """
    异常处理节点

    职责：
    - 记录错误日志
    - 重置为空闲状态
    - 后续 Step 6 完善降级策略
    """
    logger.error(f"[error_handler] 捕获异常: {state.error_message}")

    return {
        "run_mode": RunMode.IDLE,
        "is_round_complete": True,
        "error_message": None,
    }
