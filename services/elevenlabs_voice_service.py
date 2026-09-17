"""
ElevenLabs 音色 / 模型列表服务

- GET /v1/voices  拉取账号下全部可用音色（官方预置 + 你在官网创建的克隆音色），分页跟随
- GET /v1/models  拉取账号可用的 TTS 模型，并标注是否支持中文（选错 model_id 是 422 的首因）

两者均带 TTL 缓存；API 不可达（未配 Key / 网络不通）时回退到 data/elevenlabs_voices.py
与 _FALLBACK_MODELS，保证后台界面不会空白。

音色克隆本身不在这里做：ElevenLabs 的 Instant Voice Cloning 需要把音频上传到境外第三方，
且涉及声音授权合规，统一在 elevenlabs.io 官网完成，克隆好的音色会自动出现在本列表里。
"""

import time
from typing import Any, Optional

import httpx
from loguru import logger

from config.settings import settings

_CACHE_TTL = 3600  # 缓存 1 小时
_MAX_PAGES = 10    # 分页跟随上限，防止异常分页导致死循环

# voice category → 中文分组名（同时决定展示顺序：用户自建的克隆音色优先）
_CATEGORY_LABELS = {
    "cloned": "即时克隆音色 (IVC)",
    "professional": "专业克隆音色 (PVC)",
    "generated": "AI 生成音色",
    "premade": "官方预置音色",
}

# /v1/models 不可达时的兜底（supports_chinese 以官方文档口径为准）
_FALLBACK_MODELS = [
    {"model_id": "eleven_multilingual_v2", "name": "Multilingual v2", "supports_chinese": True,
     "description": "支持 29 种语言（含中文），质量最高，延迟中等"},
    {"model_id": "eleven_flash_v2_5", "name": "Flash v2.5", "supports_chinese": True,
     "description": "低延迟，支持 32 种语言，直播场景首选"},
    {"model_id": "eleven_turbo_v2_5", "name": "Turbo v2.5", "supports_chinese": False,
     "description": "低延迟，不支持中文"},
    {"model_id": "eleven_monolingual_v1", "name": "Monolingual v1", "supports_chinese": False,
     "description": "仅英语"},
]


class ElevenLabsVoiceService:
    """ElevenLabs 音色 / 模型列表服务（单例，带 TTL 缓存）"""

    _instance: Optional["ElevenLabsVoiceService"] = None

    def __init__(self):
        el = settings.elevenlabs
        self._api_key = el.api_key.strip()
        self._base_url = el.base_url.strip().rstrip("/")
        self._timeout = el.timeout
        self._connect_timeout = el.connect_timeout
        self._voices_cache: Optional[dict] = None
        self._voices_cache_ts: float = 0.0
        self._models_cache: Optional[list] = None
        self._models_cache_ts: float = 0.0

    @classmethod
    def get_instance(cls) -> "ElevenLabsVoiceService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _headers(self) -> dict:
        return {"xi-api-key": self._api_key}

    def _client(self) -> httpx.AsyncClient:
        # 列表接口很轻，超时收紧到 30s 以内，避免前端下拉框长时间转圈
        return httpx.AsyncClient(
            timeout=httpx.Timeout(min(self._timeout, 30.0), connect=self._connect_timeout)
        )

    # ================= 音色 =================

    async def fetch_voices(self, force_refresh: bool = False) -> dict:
        """获取按类别分组的音色列表；在线失败则回退静态兜底表"""
        now = time.time()
        if (
            not force_refresh
            and self._voices_cache is not None
            and now - self._voices_cache_ts < _CACHE_TTL
        ):
            logger.debug("[ElevenLabs-Voice] 使用缓存的音色列表")
            return self._voices_cache

        if not self._api_key:
            logger.warning("[ElevenLabs-Voice] ELEVENLABS_API_KEY 未配置，回退静态兜底音色表")
            return self._fallback_voices()

        try:
            async with self._client() as client:
                raw_voices = await self._fetch_all_voices(client)
            grouped = self._group_voices(raw_voices)
            if not any(grouped.values()):
                raise RuntimeError("接口返回空音色列表")
            self._voices_cache = grouped
            self._voices_cache_ts = now
            logger.info(
                f"[ElevenLabs-Voice] 在线获取 {sum(len(v) for v in grouped.values())} 个音色"
            )
            return grouped
        except Exception as e:
            logger.warning(
                f"[ElevenLabs-Voice] 在线获取失败({type(e).__name__}: {e})，回退静态兜底音色表"
            )
            return self._fallback_voices()

    async def _fetch_all_voices(self, client: httpx.AsyncClient) -> list[dict]:
        """分页拉取全部音色（has_more + last_voice_id 游标）"""
        url = f"{self._base_url}/v1/voices"
        collected: list[dict] = []
        params: dict[str, Any] = {"page_size": 100}

        for _ in range(_MAX_PAGES):
            resp = await client.get(url, params=params, headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            collected.extend(data.get("voices") or [])
            if not data.get("has_more") or not data.get("last_voice_id"):
                break
            params["last_voice_id"] = data["last_voice_id"]

        return collected

    @staticmethod
    def _group_voices(raw_voices: list[dict]) -> dict:
        """按 category 分组，字段对齐前端下拉框渲染所需（voice_id / name / style）"""
        buckets: dict[str, list] = {}
        for v in raw_voices:
            voice_id = v.get("voice_id")
            if not voice_id:
                continue
            category = v.get("category") or "premade"
            label = _CATEGORY_LABELS.get(category, category)
            labels = v.get("labels") or {}
            tags = [labels.get("gender", ""), labels.get("accent", ""), labels.get("use_case", "")]
            buckets.setdefault(label, []).append({
                "voice_id": voice_id,
                "name": v.get("name") or voice_id,
                "category": category,
                "gender": labels.get("gender", ""),
                "accent": labels.get("accent", ""),
                "style": " / ".join(t for t in tags if t),
                "preview_url": v.get("preview_url") or "",
            })

        # 固定展示顺序：克隆音色优先（用户自建的最常用），预置音色垫底，未知类别最后
        known = list(_CATEGORY_LABELS.values())
        ordered: dict[str, list] = {}
        for label in known + [k for k in buckets if k not in known]:
            if buckets.get(label):
                ordered[label] = buckets[label]
        return ordered

    @staticmethod
    def _fallback_voices() -> dict:
        from data.elevenlabs_voices import get_all_elevenlabs_voices
        return get_all_elevenlabs_voices()

    # ================= 模型 =================

    async def list_models(self, force_refresh: bool = False) -> list[dict]:
        """获取账号可用的 TTS 模型（仅 can_do_text_to_speech），并标注是否支持中文"""
        now = time.time()
        if (
            not force_refresh
            and self._models_cache is not None
            and now - self._models_cache_ts < _CACHE_TTL
        ):
            return self._models_cache

        if not self._api_key:
            logger.warning("[ElevenLabs-Voice] ELEVENLABS_API_KEY 未配置，回退兜底模型列表")
            return list(_FALLBACK_MODELS)

        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base_url}/v1/models", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

            models: list[dict] = []
            for m in resp.json() or []:
                if not m.get("can_do_text_to_speech"):
                    continue
                languages = m.get("languages") or []
                models.append({
                    "model_id": m.get("model_id"),
                    "name": m.get("name") or m.get("model_id"),
                    "description": (m.get("description") or "").strip(),
                    # language_id 形如 "zh"，中文带货直播必须挑支持中文的模型
                    "supports_chinese": any(
                        str(lang.get("language_id", "")).lower().startswith("zh")
                        for lang in languages
                    ),
                    "language_count": len(languages),
                    "max_chars_free": m.get("max_characters_request_free_user"),
                    "max_chars_paid": m.get("max_characters_request_subscribed_user"),
                })

            if not models:
                raise RuntimeError("接口未返回任何支持 TTS 的模型")

            # 支持中文的排前面，直播场景一眼可见
            models.sort(key=lambda x: (not x["supports_chinese"], x["model_id"] or ""))
            self._models_cache = models
            self._models_cache_ts = now
            logger.info(f"[ElevenLabs-Voice] 在线获取 {len(models)} 个可用 TTS 模型")
            return models
        except Exception as e:
            logger.warning(
                f"[ElevenLabs-Voice] 模型列表获取失败({type(e).__name__}: {e})，回退兜底列表"
            )
            return list(_FALLBACK_MODELS)


# 全局实例
_service = ElevenLabsVoiceService.get_instance()


async def get_elevenlabs_voices(force_refresh: bool = False) -> dict:
    """获取 ElevenLabs 音色列表（带缓存，失败回退兜底）"""
    return await _service.fetch_voices(force_refresh)


async def get_elevenlabs_models(force_refresh: bool = False) -> list[dict]:
    """获取 ElevenLabs 可用 TTS 模型列表（带缓存，失败回退兜底）"""
    return await _service.list_models(force_refresh)
