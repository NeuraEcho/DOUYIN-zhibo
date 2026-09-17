"""
OBS 推流控制路由 - 后台一键控 OBS：填推流地址+推流码、切场景、开始/停止推流

前提：OBS 28+ 已启动，且「工具 → obs-websocket 设置」里勾选了「启用 obs-websocket」。
本模块只是把你在 OBS 界面里手点的那几下自动化，不涉及任何音频/画面内容改动。
"""

from typing import Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from adapters.obs_websocket_adapter import ObsWebsocketAdapter
from common.exceptions import ObsAdapterException

router = APIRouter()


class ObsConfigRequest(BaseModel):
    """运行时修改 obs-websocket 连接配置（仅内存生效，重启回到 .env）"""
    host: Optional[str] = None
    port: Optional[int] = None
    password: Optional[str] = None
    enabled: Optional[bool] = None


class StreamServiceRequest(BaseModel):
    """写入推流地址 + 推流码（从直播伴侣「推流设置」里复制）"""
    server: str = ""   # 推流地址，如 rtmp://xxx.live-push.myqcloud.com/live
    key: str = ""      # 推流码


class SceneRequest(BaseModel):
    """切换 OBS 场景"""
    scene_name: str


class OneClickRequest(BaseModel):
    """一键开播：填推流码 → 切场景 → 开始推流"""
    server: str = ""
    key: str = ""
    scene_name: str = ""
    start_stream: bool = True


def _fail(e: Exception) -> dict:
    """统一错误返回（HTTP 200 + status=error，与项目其他路由一致）"""
    msg = e.message if isinstance(e, ObsAdapterException) else str(e)
    logger.warning(f"[obs] 操作失败: {msg}")
    return {"status": "error", "message": msg}


@router.get("/config")
async def get_config():
    """读取当前 obs-websocket 连接配置（密码不回传明文）"""
    return {"status": "ok", **ObsWebsocketAdapter.get_instance().get_config()}


@router.post("/config")
async def update_config(request: ObsConfigRequest):
    """修改 obs-websocket 连接配置（改完立即对下一次操作生效，无需重启后端）"""
    logger.info(f"[obs] 更新连接配置: host={request.host}, port={request.port}, "
                f"password={'已改' if request.password is not None else '未动'}, enabled={request.enabled}")
    try:
        cfg = ObsWebsocketAdapter.get_instance().update_config(
            host=request.host,
            port=request.port,
            password=request.password,
            enabled=request.enabled,
        )
        return {"status": "ok", "message": "配置已更新", **cfg}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@router.get("/status")
async def get_status():
    """OBS 聚合状态：连通性 + OBS 版本 + 推流状态 + 推流地址/码 + 场景列表

    连不上时不抛错，返回 connected=False + error，方便前端卡片显示「未连接」而不是报红。
    """
    adapter = ObsWebsocketAdapter.get_instance()
    cfg = adapter.get_config()
    if not cfg["enabled"]:
        return {"status": "ok", "connected": False, "error": "OBS 控制未启用", **cfg}
    try:
        data = await adapter.get_full_status()
        return {"status": "ok", **cfg, **data}
    except ObsAdapterException as e:
        return {"status": "ok", "connected": False, "error": e.message, **cfg}
    except Exception as e:  # noqa: BLE001
        return {"status": "ok", "connected": False, "error": str(e), **cfg}


@router.post("/test")
async def test_connection():
    """连通性测试（等价于握手 + GetVersion）"""
    adapter = ObsWebsocketAdapter.get_instance()
    try:
        version = await adapter.get_version()
        return {
            "status": "ok",
            "message": f"已连上 OBS {version.get('obsVersion', '?')}（obs-websocket {version.get('obsWebSocketVersion', '?')}）",
            "obs_version": version.get("obsVersion", ""),
            "obs_websocket_version": version.get("obsWebSocketVersion", ""),
        }
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@router.post("/stream/start")
async def stream_start():
    """开始推流"""
    try:
        await ObsWebsocketAdapter.get_instance().start_stream()
        return {"status": "ok", "message": "OBS 已开始推流"}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@router.post("/stream/stop")
async def stream_stop():
    """停止推流"""
    try:
        await ObsWebsocketAdapter.get_instance().stop_stream()
        return {"status": "ok", "message": "OBS 已停止推流"}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@router.post("/stream-service")
async def set_stream_service(request: StreamServiceRequest):
    """写入推流地址 + 推流码"""
    if not request.server.strip() and not request.key.strip():
        return {"status": "error", "message": "推流地址和推流码至少要填一个"}
    try:
        await ObsWebsocketAdapter.get_instance().set_stream_service_settings(
            server=request.server.strip(), key=request.key.strip()
        )
        return {"status": "ok", "message": "推流地址/推流码已写入 OBS"}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@router.get("/scenes")
async def list_scenes():
    """场景列表 + 当前播出场景"""
    adapter = ObsWebsocketAdapter.get_instance()
    try:
        scenes = await adapter.get_scene_list()
        current = await adapter.get_current_scene()
        return {
            "status": "ok",
            "scenes": [s.get("sceneName", "") for s in scenes],
            "current_scene": current,
        }
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@router.post("/scene")
async def switch_scene(request: SceneRequest):
    """切换场景"""
    if not request.scene_name.strip():
        return {"status": "error", "message": "场景名不能为空"}
    try:
        await ObsWebsocketAdapter.get_instance().set_current_scene(request.scene_name.strip())
        return {"status": "ok", "message": f"已切到场景「{request.scene_name}」"}
    except Exception as e:  # noqa: BLE001
        return _fail(e)


@router.post("/one-click")
async def one_click(request: OneClickRequest):
    """一键开播：填推流码 → 切场景 → 开始推流（每步幂等，重复点不会出错）"""
    try:
        result = await ObsWebsocketAdapter.get_instance().one_click_start(
            server=request.server,
            key=request.key,
            scene_name=request.scene_name,
            start_stream=request.start_stream,
        )
        steps = result.get("steps") or []
        return {"status": "ok", "message": "；".join(steps) or "无需任何操作", "steps": steps}
    except Exception as e:  # noqa: BLE001
        return _fail(e)
