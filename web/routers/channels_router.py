"""
视频号弹幕接收路由 - 对接 wxlivespy（微信视频号直播间弹幕抓取工具）的 HTTP 转发

背景：
- 视频号弹幕无法像抖音那样观众端直连 WebSocket，wxlivespy 用 Puppeteer 打开「视频号助手
  后台」(channels.weixin.qq.com)，主播扫码登录后监听后台的 live/msg 响应，解析出弹幕/礼物/
  进场等事件，再通过 HTTP POST 转发到本服务。
- wxlivespy 默认转发地址为 http://127.0.0.1:8000/forward（见其 src/main/config.ts），与本
  服务同端口，故本路由暴露在根路径 /forward，零配置即可对接；同时提供规范别名
  /api/channels/forward。

转发的 payload 结构（对应 wxlivespy 的 DecodedData）：
{
  "host_info": {"finder_username": ..., "wechat_uin": ...},
  "live_info": {"live_id": ..., "nickname": ..., "live_status": 1, "online_count": ..., ...},
  "events": [
    {
      "content": "弹幕内容",
      "nickname": "用户昵称",
      "decoded_type": "comment",   # comment/enter/gift/combogift/like/levelup/unknown
      "decoded_openid": "...",     # 解密后的 openid，同主播跨场次不变
      "sec_openid": "...",
      "msg_id": "...",
      "msg_time": 1699999999,
      "seq": 12                     # 消息序号，从 1 递增，可能重复推送，需服务端按 seq 去重
    }
  ]
}

对接策略（与抖音 DanmakuCollector 行为对齐）：
- 仅 decoded_type == "comment" 的事件发布为 DANMAKU_MESSAGE（对齐抖音 WebcastChatMessage），
  下游 _event_loop → DanmakuFilter 关键词/冷却过滤 → 问答/插入链路完全复用，与平台无关。
- enter/gift/combogift/levelup 仅把昵称入 ViewerPool 供「随机点名」；like 为高频事件不入池。
- gzip 转发模式（wxlivespy 配置 gzip_forward_data=true）也兼容。
"""

import gzip
import json
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Request
from loguru import logger

from scheduler.event_bus import EventBus, EventType, LiveEvent
from scheduler.viewer_pool import ViewerPool

router = APIRouter()

# seq 去重：wxlivespy 可能重复推送同一 seq；跨直播场次 seq 会从 1 重来，故按 live_id+seq 去重。
# 用 OrderedDict 做定长淘汰，避免长时间运行内存无限增长。
_seen_seq: "OrderedDict[str, bool]" = OrderedDict()
_SEEN_MAX = 4096

# 最近一次直播间状态 + 累计计数（供 /api/channels/status 调试，确认数据是否在流动）
_last_status: dict[str, Any] = {
    "live_info": None,
    "event_count": 0,
    "comment_count": 0,
    "last_event_at": 0,
    "last_comment_at": 0,
}


def _is_new_seq(live_id: str, seq: Any) -> bool:
    """按 live_id+seq 去重：新的返回 True 并记录，已见过返回 False。seq 为 None 时不去重。"""
    if seq is None:
        return True
    key = f"{live_id}:{seq}"
    if key in _seen_seq:
        return False
    _seen_seq[key] = True
    if len(_seen_seq) > _SEEN_MAX:
        _seen_seq.popitem(last=False)  # 淘汰最旧
    return True


async def _handle_forward(request: Request):
    """接收 wxlivespy 转发的视频号数据，转成 LiveEvent 发布到事件总线（复用抖音下游链路）。"""
    raw = await request.body()

    # 兼容 gzip 转发模式（wxlivespy 配置 gzip_forward_data=true 时会带 Content-Encoding: gzip）
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception as e:
            logger.warning(f"[channels] gzip 解压失败: {type(e).__name__}: {e}")
            return {"status": "error", "message": "gzip 解压失败"}

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.warning(f"[channels] 转发数据非合法 JSON: {type(e).__name__}: {e}")
        return {"status": "error", "message": "invalid json"}

    if not isinstance(data, dict):
        return {"status": "error", "message": "payload 不是对象"}

    live_info = data.get("live_info") or {}
    live_id = str(live_info.get("live_id") or "")
    events = data.get("events") or []
    if live_info:
        _last_status["live_info"] = live_info

    event_bus = EventBus.get_instance()
    viewer_pool = ViewerPool.get_instance()
    comment_accepted = 0

    for ev in events:
        if not isinstance(ev, dict):
            continue
        if not _is_new_seq(live_id, ev.get("seq")):
            continue  # 重复推送，跳过

        _last_status["event_count"] += 1
        _last_status["last_event_at"] = int(time.time())

        decoded_type = ev.get("decoded_type", "")
        nickname = (ev.get("nickname") or "").strip()
        content = (ev.get("content") or "").strip()

        if decoded_type == "comment":
            if not content:
                continue
            # 互动昵称入池，供口播稿「随机点名」{点名} 与回答带昵称使用
            viewer_pool.add(nickname)
            display_text = f"[{nickname}]: {content}" if nickname else content
            await event_bus.publish(LiveEvent(
                event_type=EventType.DANMAKU_MESSAGE,
                source="channels",           # 非 preview_danmaku → 下游照常走关键词/冷却过滤
                content=display_text,
                metadata={
                    "user_name": nickname,
                    "raw_content": content,   # 下游 _event_loop 取此字段作为提问内容
                    "raw_data": ev,
                    "platform": "wechat_channels",
                    "decoded_openid": ev.get("decoded_openid", ""),
                },
            ))
            comment_accepted += 1
            _last_status["comment_count"] += 1
            _last_status["last_comment_at"] = int(time.time())
            logger.info(f"[channels] 视频号弹幕: {display_text}")
        elif decoded_type in ("enter", "gift", "combogift", "levelup"):
            # 进场/送礼/升级：仅把昵称入池供点名，不触发问答（与抖音采集器一致）
            if nickname:
                viewer_pool.add(nickname)
        # like：高频事件，仅计数不入池（避免刷屏挤占昵称池），与抖音采集器策略一致

    return {"status": "ok", "accepted": comment_accepted, "events": len(events)}


@router.post("/forward")
async def forward_default(request: Request):
    """wxlivespy 默认转发地址（http://127.0.0.1:8000/forward），零配置直连。"""
    return await _handle_forward(request)


@router.post("/api/channels/forward")
async def forward_canonical(request: Request):
    """规范路径别名，等价于 /forward（若你把 wxlivespy 转发地址改成此路径也可用）。"""
    return await _handle_forward(request)


@router.get("/api/channels/status")
async def channels_status():
    """查看视频号弹幕接收状态（最近直播间信息 + 累计计数），供调试确认数据是否在流动。"""
    return _last_status
