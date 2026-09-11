"""
FastAPI 应用入口 - Web 管理后台 API 服务
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from web.routers import config_router, control_router, monitor_router, voice_clone_router, preview_router, tts_debug_router

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title="AI 云端口播直播系统",
        description="基于 LangGraph 混合架构的 AI 直播主播管理后台",
        version="0.1.0",
    )

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(control_router.router, prefix="/api/live", tags=["直播控制"])
    app.include_router(config_router.router, prefix="/api/config", tags=["配置管理"])
    app.include_router(monitor_router.router, prefix="/api/monitor", tags=["实时监控"])
    app.include_router(voice_clone_router.router, prefix="/api/voice-clone", tags=["音色克隆"])
    app.include_router(preview_router.router, prefix="/api/preview", tags=["试听试播"])
    app.include_router(tts_debug_router.router, prefix="", tags=["TTS调试工具"])

    # 静态文件 & 首页
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = _STATIC_DIR / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>AI 云端口播直播系统</h1><p>UI 文件未找到</p>")

    @app.on_event("startup")
    async def startup():
        logger.info("[Web] FastAPI 应用启动")

    @app.on_event("shutdown")
    async def shutdown():
        logger.info("[Web] FastAPI 应用关闭")

    return app
