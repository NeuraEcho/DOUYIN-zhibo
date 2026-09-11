"""
火山引擎音色列表服务 + 声音复刻（克隆）音色查询

复刻音色查询依据官方「音色查询HTTP」接口（doc 6561/2535742）：
    POST https://openspeech.bytedance.com/api/v3/tts/get_voice
    Header: Content-Type / X-Api-Key / X-Api-Request-Id
    Body:   {"speaker_id": "S_xxx"}
该接口按单个 speaker_id 查询训练状态，无「列出全部」能力（批量列表需 AKSK 签名），
因此本地维护一份已注册复刻音色表，逐个查询状态后展示。
"""
import json
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# 官方「音色查询HTTP」接口
_GET_VOICE_URL = "https://openspeech.bytedance.com/api/v3/tts/get_voice"
# 本地已注册复刻音色存储
_CLONED_DB_PATH = Path("./data/volcengine_cloned_voices.json")

# status 枚举（2/4 可调用 TTS 合成）
_STATUS_MAP = {
    0: ("notfound", "未找到"),
    1: ("training", "训练中"),
    2: ("success", "可用"),
    3: ("failed", "训练失败"),
    4: ("active", "已激活"),
}
# language 枚举
_LANGUAGE_MAP = {
    0: "中文", 1: "英文", 2: "日语", 3: "西班牙语", 4: "印尼语", 5: "葡萄牙语",
    6: "德语", 7: "法语", 8: "韩语", 9: "意大利语", 10: "泰语", 11: "越南语",
    12: "俄语", 13: "菲律宾语", 14: "马来语", 15: "阿拉伯语", 16: "墨西哥西班牙语",
    17: "巴西葡萄牙语", 19: "波兰语", 20: "土耳其语", 21: "瑞典语",
}


class VolcengineVoiceService:
    """火山引擎音色服务"""

    _instance = None
    _voices_cache: Optional[dict] = None
    _cache_ttl = 3600  # 缓存 1 小时

    def __init__(self):
        self._api_key = settings.volcengine.api_key.strip()
        self._app_id = settings.volcengine.app_id.strip()
        self._cloned_voices: dict[str, dict] = {}
        self._load_cloned_voices()

    @classmethod
    def get_instance(cls) -> "VolcengineVoiceService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def fetch_voices_from_api(self, force_refresh: bool = False) -> dict:
        """
        获取音色列表

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            按场景分组的音色字典
        """
        if not force_refresh and self._voices_cache:
            logger.info("[Volcengine-Voice] 使用缓存的音色列表")
            return self._voices_cache

        try:
            logger.info("[Volcengine-Voice] 使用本地 uranus 大模型音色列表（seed-tts-2.0）")
            voices = self._get_default_voices()
            self._voices_cache = voices
            total = sum(len(v) for v in voices.values())
            logger.info(f"[Volcengine-Voice] 成功获取 {total} 个音色")
            return voices
        except Exception as e:
            logger.warning(f"[Volcengine-Voice] 获取失败: {e}")
            return {}

    def _get_default_voices(self) -> dict:
        """获取本地默认音色列表"""
        from data.volcengine_voices import get_all_volcengine_voices
        return get_all_volcengine_voices()

    async def get_voice_by_id(self, voice_id: str) -> Optional[dict]:
        """根据 voice_id 获取单个音色信息"""
        voices = await self.fetch_voices_from_api()

        for category, voice_list in voices.items():
            for voice in voice_list:
                if voice["voice_id"] == voice_id:
                    return voice

        return None

    # ================= 声音复刻（克隆）音色 =================

    def _load_cloned_voices(self):
        """从本地文件加载已注册复刻音色"""
        if _CLONED_DB_PATH.exists():
            try:
                data = json.loads(_CLONED_DB_PATH.read_text(encoding="utf-8"))
                self._cloned_voices = data if isinstance(data, dict) else {}
                logger.info(f"[Volcengine-Voice] 加载 {len(self._cloned_voices)} 个复刻音色")
            except Exception as e:
                logger.warning(f"[Volcengine-Voice] 加载复刻音色失败: {e}")

    def _save_cloned_voices(self):
        """持久化复刻音色表"""
        _CLONED_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CLONED_DB_PATH.write_text(
            json.dumps(self._cloned_voices, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def query_voice_status(self, speaker_id: str) -> dict:
        """
        调用官方「音色查询HTTP」接口查询单个复刻音色状态

        POST /api/v3/tts/get_voice（X-Api-Key 鉴权）
        :return: 标准化 dict（含 status/usable/demo_audio/language 等）
        """
        speaker_id = (speaker_id or "").strip()
        if not speaker_id:
            raise ValueError("speaker_id 不能为空")
        if not self._api_key:
            raise ValueError("VOLCENGINE_API_KEY 未配置")

        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self._api_key,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        payload = {"speaker_id": speaker_id}

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(_GET_VOICE_URL, json=payload, headers=headers)
            logid = resp.headers.get("x-tt-logid", "N/A")
            if resp.status_code != 200:
                raise RuntimeError(
                    f"查询失败 HTTP {resp.status_code}: {resp.text[:200]} (logid={logid})"
                )
            data = resp.json()

        code = data.get("code", 0)
        if code != 0:
            raise RuntimeError(
                f"查询失败 code={code}: {data.get('message', '')} (logid={logid})"
            )

        status = int(data.get("status", 0))
        status_key, status_text = _STATUS_MAP.get(status, ("unknown", f"未知({status})"))
        language = int(data.get("language", 0))

        # 提取 demo_audio / model_type（优先复刻2.0 model_type=5）
        demo_audio = ""
        model_type = None
        speaker_status = data.get("speaker_status") or []
        if isinstance(speaker_status, list) and speaker_status:
            chosen = speaker_status[0]
            for ss in speaker_status:
                if isinstance(ss, dict) and ss.get("model_type") == 5:
                    chosen = ss
                    break
            if isinstance(chosen, dict):
                demo_audio = chosen.get("demo_audio", "") or ""
                model_type = chosen.get("model_type")

        return {
            "speaker_id": data.get("speaker_id", speaker_id),
            "status": status,
            "status_key": status_key,
            "status_text": status_text,
            "usable": status in (2, 4),
            "language": language,
            "language_text": _LANGUAGE_MAP.get(language, f"未知({language})"),
            "create_time": data.get("create_time", 0),
            "available_training_times": data.get("available_training_times", 0),
            "model_type": model_type,
            "demo_audio": demo_audio,
        }

    async def add_cloned_voice(self, speaker_id: str, name: str = "") -> dict:
        """查询并注册一个复刻音色到本地列表"""
        info = await self.query_voice_status(speaker_id)
        record = dict(info)
        record["name"] = (name or "").strip() or info["speaker_id"]
        record["added_at"] = int(time.time() * 1000)
        self._cloned_voices[info["speaker_id"]] = record
        self._save_cloned_voices()
        logger.info(
            f"[Volcengine-Voice] 已注册复刻音色: {info['speaker_id']} "
            f"状态={info['status_text']} 可用={info['usable']}"
        )
        return record

    async def refresh_cloned_voices(self) -> list:
        """刷新所有已注册复刻音色的状态"""
        for sid in list(self._cloned_voices.keys()):
            try:
                info = await self.query_voice_status(sid)
                self._cloned_voices[sid].update(info)
            except Exception as e:
                logger.warning(f"[Volcengine-Voice] 刷新 {sid} 失败: {e}")
        self._save_cloned_voices()
        return list(self._cloned_voices.values())

    def list_cloned_voices(self) -> list:
        """本地已注册复刻音色列表"""
        return list(self._cloned_voices.values())

    def get_cloned_voice(self, speaker_id: str) -> Optional[dict]:
        return self._cloned_voices.get(speaker_id)

    def delete_cloned_voice(self, speaker_id: str) -> bool:
        if speaker_id not in self._cloned_voices:
            return False
        del self._cloned_voices[speaker_id]
        self._save_cloned_voices()
        logger.info(f"[Volcengine-Voice] 已删除复刻音色: {speaker_id}")
        return True


# 全局实例
_service = VolcengineVoiceService.get_instance()


async def get_volcengine_voices(force_refresh: bool = False) -> dict:
    """获取火山引擎音色列表（带缓存）"""
    return await _service.fetch_voices_from_api(force_refresh)


async def get_volcengine_voice_by_id(voice_id: str) -> Optional[dict]:
    """根据 ID 获取单个音色"""
    return await _service.get_voice_by_id(voice_id)


async def query_volcengine_cloned_voice(speaker_id: str, name: str = "") -> dict:
    """查询并注册火山复刻音色"""
    return await _service.add_cloned_voice(speaker_id, name)


async def refresh_volcengine_cloned_voices() -> list:
    """刷新全部已注册复刻音色状态"""
    return await _service.refresh_cloned_voices()


def list_volcengine_cloned_voices() -> list:
    """本地已注册复刻音色列表"""
    return _service.list_cloned_voices()


def get_volcengine_cloned_voice(speaker_id: str) -> Optional[dict]:
    return _service.get_cloned_voice(speaker_id)


def delete_volcengine_cloned_voice(speaker_id: str) -> bool:
    return _service.delete_cloned_voice(speaker_id)
