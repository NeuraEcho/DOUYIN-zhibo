"""
MiniMax 音色克隆服务
封装上传音频、调用克隆接口、管理克隆音色等完整流程
"""

import json
import uuid
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# 克隆音色元数据存储路径
_VOICES_DB_PATH = Path("./data/cloned_voices.json")

# 允许的音频格式
ALLOWED_EXTENSIONS = {".mp3", ".m4a", ".wav"}
# 复刻音频：10s ~ 5min, ≤20MB
CLONE_MIN_DURATION = 10
CLONE_MAX_DURATION = 300
CLONE_MAX_SIZE = 20 * 1024 * 1024
# 示例音频：<8s, ≤20MB
PROMPT_MAX_DURATION = 8
PROMPT_MAX_SIZE = 20 * 1024 * 1024


class ClonedVoice:
    """克隆音色元数据"""

    def __init__(
        self,
        voice_id: str,
        name: str,
        file_id: str,
        prompt_file_id: Optional[str] = None,
        prompt_text: Optional[str] = None,
        model: str = "speech-2.8-hd",
        preview_url: Optional[str] = None,
    ):
        self.voice_id = voice_id
        self.name = name
        self.file_id = file_id
        self.prompt_file_id = prompt_file_id
        self.prompt_text = prompt_text
        self.model = model
        self.preview_url = preview_url

    def to_dict(self) -> dict:
        return {
            "voice_id": self.voice_id,
            "name": self.name,
            "file_id": self.file_id,
            "prompt_file_id": self.prompt_file_id,
            "prompt_text": self.prompt_text,
            "model": self.model,
            "preview_url": self.preview_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClonedVoice":
        return cls(**{k: v for k, v in data.items() if k in cls.__init__.__code__.co_varnames})


class VoiceCloneService:
    """
    MiniMax 音色克隆服务（单例）

    流程：
    1. 上传参考音频 → 获取 file_id
    2. (可选) 上传示例音频 → 获取 prompt_file_id
    3. 调用克隆接口 → 获得 voice_id + 试听音频
    4. 本地持久化音色元数据
    """

    _instance: Optional["VoiceCloneService"] = None

    def __init__(self):
        self._api_key = settings.speech.api_key.strip()
        self._base_url = "https://api.minimax.cn"
        self._client: Optional[httpx.AsyncClient] = None
        self._voices: dict[str, ClonedVoice] = {}
        self._load_voices()

        # 诊断：检查 API Key 是否加载成功
        if not self._api_key:
            logger.error(
                "[VoiceClone] SPEECH_API_KEY 为空！请检查 .env 文件中 SPEECH_API_KEY 是否正确配置"
            )
        else:
            logger.info(
                f"[VoiceClone] API Key 已加载: {self._api_key[:8]}...{self._api_key[-4:]}"
            )

    @classmethod
    def get_instance(cls) -> "VoiceCloneService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=httpx.Timeout(120.0, connect=15.0),
            )
        return self._client

    # ---------- 本地持久化 ----------

    def _load_voices(self):
        """从本地文件加载已克隆音色列表"""
        if _VOICES_DB_PATH.exists():
            try:
                data = json.loads(_VOICES_DB_PATH.read_text(encoding="utf-8"))
                self._voices = {
                    vid: ClonedVoice.from_dict(v) for vid, v in data.items()
                }
                logger.info(f"[VoiceClone] 加载 {len(self._voices)} 个已克隆音色")
            except Exception as e:
                logger.warning(f"[VoiceClone] 加载音色数据失败: {e}")

    def _save_voices(self):
        """持久化音色列表到本地文件"""
        _VOICES_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {vid: v.to_dict() for vid, v in self._voices.items()}
        _VOICES_DB_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 音频上传 ----------

    async def upload_clone_audio(self, filename: str, file_data: bytes) -> str:
        """
        上传待克隆的参考音频

        :param filename: 文件名（含扩展名）
        :param file_data: 音频二进制数据
        :return: file_id
        """
        self._validate_audio_file(filename, file_data, is_clone=True)

        client = self._get_client()
        files = {"file": (filename, file_data)}
        data = {"purpose": "voice_clone"}

        logger.info(f"[VoiceClone] 上传参考音频: {filename}, 大小={len(file_data)} bytes")
        resp = await client.post("/v1/files/upload", data=data, files=files)
        resp.raise_for_status()
        result = resp.json()

        file_id = result.get("file", {}).get("file_id")
        if not file_id:
            raise RuntimeError(f"上传失败，未返回 file_id: {result}")

        # file_id 保持原始类型（MiniMax 要求 integer）
        logger.info(f"[VoiceClone] 参考音频上传成功, file_id={file_id}")
        return str(file_id)  # 返回字符串用于存储和传输

    async def upload_prompt_audio(self, filename: str, file_data: bytes) -> str:
        """
        上传示例音频（可选，用于增强克隆效果）

        :param filename: 文件名
        :param file_data: 音频二进制数据
        :return: file_id
        """
        self._validate_audio_file(filename, file_data, is_clone=False)

        client = self._get_client()
        files = {"file": (filename, file_data)}
        data = {"purpose": "prompt_audio"}

        logger.info(f"[VoiceClone] 上传示例音频: {filename}, 大小={len(file_data)} bytes")
        resp = await client.post("/v1/files/upload", data=data, files=files)
        resp.raise_for_status()
        result = resp.json()

        file_id = result.get("file", {}).get("file_id")
        if not file_id:
            raise RuntimeError(f"上传失败，未返回 file_id: {result}")

        # file_id 保持原始类型（MiniMax 要求 integer）
        logger.info(f"[VoiceClone] 示例音频上传成功, file_id={file_id}")
        return str(file_id)  # 返回字符串用于存储和传输

    @staticmethod
    def _validate_audio_file(filename: str, data: bytes, is_clone: bool):
        """校验音频文件格式和大小"""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持的音频格式: {ext}，仅支持 {ALLOWED_EXTENSIONS}")

        max_size = CLONE_MAX_SIZE if is_clone else PROMPT_MAX_SIZE
        if len(data) > max_size:
            raise ValueError(f"文件大小 {len(data)} bytes 超过限制 {max_size} bytes (20MB)")

    # ---------- 音色克隆 ----------

    async def clone_voice(
        self,
        voice_id: str,
        name: str,
        file_id: str,
        prompt_file_id: Optional[str] = None,
        prompt_text: Optional[str] = None,
        text: str = "",
        model: str = "speech-2.8-hd",
        language_boost: str = "Chinese",
        need_noise_reduction: bool = True,
        need_volume_normalization: bool = True,
    ) -> dict:
        """
        调用 MiniMax 音色克隆接口（高质量模式）

        :param voice_id: 自定义音色 ID（唯一标识）
        :param name: 音色显示名称
        :param file_id: 参考音频 file_id
        :param prompt_file_id: 示例音频 file_id（可选，增强克隆效果）
        :param prompt_text: 示例音频对应的文本（可选）
        :param text: 试听文本（用于生成试听音频）
        :param model: 克隆模型，默认 speech-2.8-hd（高保真）
        :param language_boost: 语言增强，默认 Chinese
        :param need_noise_reduction: 是否开启降噪（推荐 true）
        :param need_volume_normalization: 是否开启音量归一化（推荐 true）
        :return: 克隆结果（含试听音频 URL）
        """
        client = self._get_client()

        # 默认试听文本（MiniMax 克隆需要 text + model 才能正常工作）
        if not text:
            text = "你好，欢迎收听"

        # 最多重试 3 次（处理 voice_id 重复）
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            current_voice_id = voice_id if attempt == 0 else self.generate_voice_id(name)

            # file_id 转为 integer（MiniMax API 要求）
            payload: dict = {
                "file_id": int(file_id),
                "voice_id": current_voice_id,
            }

            # 可选：示例音频增强（prompt_audio 也必须为 integer）
            if prompt_file_id:
                clone_prompt: dict = {
                    "prompt_audio": int(prompt_file_id),
                }
                if prompt_text:
                    clone_prompt["prompt_text"] = prompt_text
                payload["clone_prompt"] = clone_prompt

            # 试听文本（限制 1000 字符）+ model（提供 text 时必填）
            payload["text"] = text[:1000]
            payload["model"] = model

            # 高质量克隆参数
            if language_boost:
                payload["language_boost"] = language_boost
            if need_noise_reduction:
                payload["need_noise_reduction"] = True
            if need_volume_normalization:
                payload["need_volume_normalization"] = True

            logger.info(
                f"[VoiceClone] 开始克隆音色(HD): voice_id={current_voice_id}, name={name}, "
                f"file_id={file_id}, model={model}, 降噪={need_noise_reduction}, "
                f"音量归一化={need_volume_normalization}, 语言增强={language_boost}, "
                f"attempt={attempt + 1}/{max_retries}"
            )

            resp = await client.post(
                "/v1/voice_clone",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            result = resp.json()

            # 检查 API 错误
            base_resp = result.get("base_resp", {})
            status_code = base_resp.get("status_code", 0)

            if status_code == 0:
                # 成功
                preview_url = result.get("demo_audio", "")
                voice = ClonedVoice(
                    voice_id=current_voice_id,
                    name=name,
                    file_id=file_id,
                    prompt_file_id=prompt_file_id,
                    prompt_text=prompt_text,
                    model=model,
                    preview_url=preview_url,
                )
                self._voices[current_voice_id] = voice
                self._save_voices()

                logger.info(
                    f"[VoiceClone] 音色克隆成功: voice_id={current_voice_id}, name={name}"
                )
                return {
                    "voice_id": current_voice_id,
                    "name": name,
                    "preview_url": preview_url,
                    "model": model,
                }

            elif status_code == 2039:
                # voice_id 重复，自动重试
                logger.warning(
                    f"[VoiceClone] voice_id 重复: {current_voice_id}，"
                    f"将自动生成新 ID 重试"
                )
                last_error = f"voice_id 重复: {current_voice_id}"
                continue
            else:
                # 其他错误
                status_msg = base_resp.get("status_msg", "未知错误")
                raise RuntimeError(f"克隆失败 (错误码 {status_code}): {status_msg}")

        # 所有重试均失败
        raise RuntimeError(f"克隆失败: voice_id 重复多次，最后一次: {last_error}")

    # ---------- 音色管理 ----------

    def list_voices(self) -> list[dict]:
        """获取所有已克隆音色列表"""
        return [v.to_dict() for v in self._voices.values()]

    def get_voice(self, voice_id: str) -> Optional[dict]:
        """获取指定音色详情"""
        voice = self._voices.get(voice_id)
        return voice.to_dict() if voice else None

    def delete_voice(self, voice_id: str) -> bool:
        """删除指定克隆音色"""
        if voice_id not in self._voices:
            return False
        del self._voices[voice_id]
        self._save_voices()
        logger.info(f"[VoiceClone] 已删除音色: voice_id={voice_id}")
        return True

    def generate_voice_id(self, name: str) -> str:
        """自动生成唯一 voice_id（仅 ASCII 字符，符合 MiniMax 规范）"""
        # 只保留 ASCII 字母、数字，其余替换为下划线
        safe_name = ""
        for c in name:
            if c.isascii() and (c.isalnum() or c in "-_"):
                safe_name += c
            else:
                safe_name += "_"
        # 去除首尾的下划线和连字符
        safe_name = safe_name.strip("-_")
        if not safe_name:
            safe_name = "voice"
        short_uuid = uuid.uuid4().hex[:8]
        voice_id = f"{safe_name}_{short_uuid}"
        # 确保长度 >= 8
        if len(voice_id) < 8:
            voice_id = voice_id + "_" + uuid.uuid4().hex[:8]
        return voice_id

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
