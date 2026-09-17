"""
用量与成本统计 - 实时累计 TTS 计费字符 / LLM token，估算费用
供前端「监控」页成本看板轮询展示。

计费口径（以各厂商控制台实价为准，可在 PRICING 中调整）：
- MiniMax speech-2.8-turbo：2 元/万字符，官方规则 1 汉字=2 字符
- MiniMax speech-2.8-hd：3.5 元/万字符（若用 hd，把 minimax 单价改 3.5）
- 火山引擎 豆包 Seed-TTS 2.0：约 3 元/万字符
- ElevenLabs：1 字符 = 1 credit，按 Creator 档（$22/10万 credit）≈ 16 元/万字符，请按实际套餐调整
- DeepSeek（LLM）：输入/输出按 token 计费，此处用字符估算 token（1 token≈1.6 汉字）

所有 TTS/LLM 调用都经过适配器 synthesize/stream_chat，故在适配器内埋点即可全覆盖。
"""

import threading
import time
from typing import Optional

# ===== 计费单价（可按控制台实价 / 代理价调整）=====
PRICING = {
    "tts": {                 # 元 / 万计费字符
        "minimax": 2.0,      # speech-2.8-turbo；若用 hd 改 3.5
        "volcengine": 3.0,   # 豆包 Seed-TTS 2.0 大模型语音合成（按量）
        "elevenlabs": 16.0,  # 1 字符=1 credit；按 Creator 档 $22/10万 credit 估算，套餐不同请自行调整
    },
    "llm": {                 # 元 / 百万 token
        "input": 2.0,
        "output": 8.0,
    },
}

# 字符 → token 估算比（1 token ≈ 1.6 个汉字，厂商公开口径）
_CHARS_PER_TOKEN = 1.6


def _is_han(c: str) -> bool:
    """是否 CJK 汉字（MiniMax 计费规则：汉字算 2 字符）"""
    return "\u4e00" <= c <= "\u9fff"


def _minimax_billing_chars(text: str) -> int:
    """MiniMax 计费字符：1 汉字=2 字符，其余（英文/数字/标点/空格）=1 字符"""
    return sum(2 if _is_han(c) else 1 for c in text)


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}小时{m}分"
    if m > 0:
        return f"{m}分{s}秒"
    return f"{s}秒"


class UsageTracker:
    """全局用量统计（单例，线程安全）"""

    _instance: Optional["UsageTracker"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._reset()

    def _reset(self):
        self._start_ts = time.time()
        self._tts_calls = 0
        self._tts_raw_chars = 0
        self._tts_billing_chars = 0
        self._tts_by_provider: dict = {}
        self._llm_calls = 0
        self._llm_input_chars = 0
        self._llm_output_chars = 0

    @classmethod
    def get_instance(cls) -> "UsageTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ===== 埋点：TTS =====
    def record_tts(self, provider: str, text: str):
        """在 TTS 适配器 synthesize() 内调用，累计计费字符与费用"""
        if not text:
            return
        raw = len(text)
        # MiniMax 汉字算 2 字符；火山与 ElevenLabs 均按原始字符数计费
        billing = _minimax_billing_chars(text) if provider == "minimax" else raw
        unit = PRICING["tts"].get(provider, 0.0)
        cost = billing / 10000.0 * unit
        with self._lock:
            self._tts_calls += 1
            self._tts_raw_chars += raw
            self._tts_billing_chars += billing
            p = self._tts_by_provider.setdefault(
                provider, {"calls": 0, "raw_chars": 0, "billing_chars": 0, "cost": 0.0}
            )
            p["calls"] += 1
            p["raw_chars"] += raw
            p["billing_chars"] += billing
            p["cost"] += cost

    # ===== 埋点：LLM =====
    def record_llm(self, model: str, input_text: str, output_text: str):
        """在 DeepSeek 适配器 stream_chat/stream_chat_iterator 内调用"""
        in_chars = len(input_text or "")
        out_chars = len(output_text or "")
        if in_chars == 0 and out_chars == 0:
            return
        with self._lock:
            self._llm_calls += 1
            self._llm_input_chars += in_chars
            self._llm_output_chars += out_chars

    def reset(self):
        with self._lock:
            self._reset()

    # ===== 查询 =====
    def get_usage(self) -> dict:
        with self._lock:
            elapsed = time.time() - self._start_ts
            in_tokens = self._llm_input_chars / _CHARS_PER_TOKEN
            out_tokens = self._llm_output_chars / _CHARS_PER_TOKEN
            llm_cost = (
                in_tokens / 1e6 * PRICING["llm"]["input"]
                + out_tokens / 1e6 * PRICING["llm"]["output"]
            )
            tts_cost = sum(p["cost"] for p in self._tts_by_provider.values())
            total_cost = tts_cost + llm_cost
            hours = elapsed / 3600.0
            cost_per_hour = total_cost / hours if hours > 0 else 0.0
            by_provider = {
                k: {
                    "calls": v["calls"],
                    "raw_chars": v["raw_chars"],
                    "billing_chars": v["billing_chars"],
                    "cost": round(v["cost"], 4),
                }
                for k, v in self._tts_by_provider.items()
            }
            return {
                "elapsed_seconds": round(elapsed, 1),
                "elapsed_text": _fmt_duration(elapsed),
                "tts": {
                    "calls": self._tts_calls,
                    "raw_chars": self._tts_raw_chars,
                    "billing_chars": self._tts_billing_chars,
                    "cost": round(tts_cost, 4),
                    "by_provider": by_provider,
                },
                "llm": {
                    "calls": self._llm_calls,
                    "input_chars": self._llm_input_chars,
                    "output_chars": self._llm_output_chars,
                    "input_tokens": int(in_tokens),
                    "output_tokens": int(out_tokens),
                    "cost": round(llm_cost, 4),
                },
                "total_cost": round(total_cost, 4),
                "cost_per_hour": round(cost_per_hour, 4),
                "projected_8h_cost": round(cost_per_hour * 8, 2),
                "pricing": PRICING,
            }
