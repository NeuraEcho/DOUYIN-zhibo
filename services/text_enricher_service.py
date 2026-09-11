"""
LLM 文本口语化标注服务
用大模型将书面文案转化为带有停顿、语气词、声音动作标签的口语化文本，
使 TTS 合成效果更接近真人直播。

MiniMax TTS 支持的标注语法：
- 停顿标记：<#秒数#>  例如 <#0.2#> <#0.3#> <#0.4#>
- 语气词标签（完整列表）：
  (breath) 换气、(chuckle) 轻笑、(laughs) 笑声、(sighs) 叹气、
  (clear-throat) 清嗓子、(coughs) 咳嗽、(emm) 嗯 等
"""

import re
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

# 数字转中文映射
_DIGIT_MAP = {
    '0': '零', '1': '一', '2': '二', '3': '三', '4': '四',
    '5': '五', '6': '六', '7': '七', '8': '八', '9': '九'
}
_UNIT_MAP = ['', '十', '百', '千', '万', '十', '百', '千', '亿']


def number_to_chinese(num_str: str) -> str:
    """
    将阿拉伯数字转换为中文读法
    例如：99 → 九十九、100 → 一百、999 → 九百九十九
    """
    try:
        num = int(num_str)
    except ValueError:
        return num_str
    
    if num == 0:
        return '零'
    
    # 处理负数
    result = ''
    if num < 0:
        result = '负'
        num = -num
    
    # 转换数字
    num_str = str(num)
    length = len(num_str)
    
    # 简单处理：直接逐位转换（适用于直播场景的简单数字）
    if length <= 2:
        if length == 2:
            tens = int(num_str[0])
            ones = int(num_str[1])
            if tens == 1:
                result += '十'
            elif tens > 1:
                result += _DIGIT_MAP[str(tens)] + '十'
            if ones > 0:
                result += _DIGIT_MAP[str(ones)]
            return result if result else '零'
        else:
            return _DIGIT_MAP[num_str] if num_str in _DIGIT_MAP else num_str
    
    # 更长的数字，逐位读出（直播场景更自然）
    # 例如：999 → 九百九十九、1234 → 一千二百三十四
    chars = []
    for i, digit in enumerate(num_str):
        pos = length - 1 - i
        if digit == '0':
            if chars and chars[-1] != '零' and pos > 0:
                chars.append('零')
        else:
            chars.append(_DIGIT_MAP[digit])
            if pos > 0:
                chars.append(_UNIT_MAP[pos])
    
    # 清理末尾的零
    result_chars = []
    for c in chars:
        if result_chars or c != '零':
            result_chars.append(c)
    
    if result_chars and result_chars[-1] == '零':
        result_chars.pop()
    
    return result + ''.join(result_chars)


def normalize_text_for_tts(text: str) -> str:
    """
    TTS 文本预处理：数字转中文、清理 markdown 和特殊符号
    """
    # 1. 删除 markdown 标记
    text = re.sub(r'[#*`_~]', '', text)
    
    # 2. 压缩多余换行
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # 3. 数字转中文（匹配连续数字）
    def replace_num(match):
        return number_to_chinese(match.group())
    
    text = re.sub(r'\d+', replace_num, text)
    
    return text

# 口语化标注系统 Prompt（基于真实开发者验证的最佳实践）
ENRICH_SYSTEM_PROMPT = """你是一个专业的直播口播文案标注师。你的任务是将书面文案转化为自然口语化的直播话术，让 TTS 合成后听起来像真人主播。

## MiniMax 支持的语气词标签（完整列表）
(breath) 正常换气、(chuckle) 轻笑【推荐直播用】、(laughs) 笑声【慎用】、
(coughs) 咳嗽、(clear-throat) 清嗓子、(groans) 呻吟、
(pant) 喘气、(inhale) 吸气、(exhale) 呼气、(gasps) 倒吸气、
(sniffs) 吸鼻子、(sighs) 叹气、(snorts) 喷鼻息、
(burps) 打嗝、(lip-smacking) 咂嘴、(humming) 哼唱、
(hissing) 嘶嘶声、(emm) 嗯、(sneezes) 喷嚏

## 标注规则（两个维度同时做）

### 1. 语气词标签（最重要，消除机械感）
- (breath)：每3-5句出现一次，放在句子开头或语义停顿处
- (chuckle)：直播带货首选，温和自然，用于福利、互动弹幕回复
- 不要滥用！每3-6句话最多1个标签，不要连续堆砌
- 直播场景优先用 (breath) 和 (chuckle)，少用 (laughs) 大笑

### 2. 口语化改写
- 书面语转口语："因此"→"所以说"、"然而"→"但是呢"
- 加入语气词："嗯"、"对吧"、"你们知道吗"、"跟大家说"
- 加入互动引导："对不对"、"是吧"、"你们觉得呢"
- 数字全部转中文：99→九十九、100→一百
- 单句控制在20字以内

## 输出要求
- 直接输出标注后的文本，不要加任何解释或前缀
- 保持原文的核心信息和语义不变
- 语气词不要过度堆砌，保持自然
- 不要使用 `<#秒数#>` 停顿标记，用标点和换行自然停顿

## 真实带货场景示例

示例输入：这款产品采用了先进的降噪技术，音质非常出色，续航时间长达30小时，现在只要999元。
示例输出：跟大家说下哈，
(breath)，咱们这个耳机用了特别先进的降噪技术。
音质真的绝了，
续航时间长达三十个小时(chuckle)。
现在只要九百九十九元，想要的家人抓紧拍。

示例输入：欢迎新进来的朋友，喜欢可以停留一会，有家人问尺码，从小码到加大码全都有。
示例输出：欢迎刚进来的朋友，
(breath)，喜欢可以稍微停留一会。
有家人问尺码对吧，
咱们这个尺码，从小码到加大码全都有。
身上不挑身材(chuckle)。"""


class TextEnricherService:
    """
    LLM 文本口语化标注服务（单例）

    使用 DeepSeek LLM 将书面文案转化为带有停顿标记、语气词、
    情感标记的口语化文本，使 TTS 合成效果更像真人。
    """

    _instance: Optional["TextEnricherService"] = None
    _enabled: bool = True  # 默认开启

    def __init__(self):
        self._api_key = settings.deepseek.api_key.strip()
        self._base_url = settings.deepseek.base_url.strip()
        self._model = settings.deepseek.model.strip()
        self._client: Optional[httpx.AsyncClient] = None

    @classmethod
    def get_instance(cls) -> "TextEnricherService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def set_enabled(cls, enabled: bool):
        cls._enabled = enabled
        logger.info(f"[TextEnricher] 口语化标注已{'开启' if enabled else '关闭'}")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        return self._client

    async def enrich_text(self, text: str) -> str:
        """
        将单段文本口语化标注

        :param text: 原始书面文案
        :return: 带停顿标记和语气词的口语化文本
        """
        if not self._enabled or not text.strip():
            return text

        client = self._get_client()
        messages = [
            {"role": "system", "content": ENRICH_SYSTEM_PROMPT},
            {"role": "user", "content": f"请将以下文案口语化标注：\n\n{text}"},
        ]

        request_body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.8,  # 稍高温度让输出更自然多变
            "stream": False,  # 非流式，需要完整结果
        }

        try:
            logger.info(f"[TextEnricher] 开始口语化标注, 输入长度={len(text)}")

            resp = await client.post("/chat/completions", json=request_body)
            resp.raise_for_status()
            result = resp.json()

            enriched = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            enriched = enriched.strip()

            if not enriched:
                logger.warning("[TextEnricher] LLM 返回空结果，使用原文")
                return text

            logger.info(
                f"[TextEnricher] 标注完成, "
                f"输入={len(text)}字 → 输出={len(enriched)}字"
            )
            return enriched

        except Exception as e:
            logger.error(f"[TextEnricher] 口语化标注失败: {e}，使用原文")
            return text

    async def enrich_sentences(self, sentences: list[str]) -> list[str]:
        """
        批量标注多个句子（一次 LLM 调用处理整段）

        :param sentences: 句子列表
        :return: 标注后的句子列表
        """
        if not self._enabled or not sentences:
            return sentences

        # 合并为一段进行标注（减少 LLM 调用次数）
        combined = "\n".join(sentences)
        enriched = await self.enrich_text(combined)

        # 按换行拆分回句子
        enriched_sentences = [s.strip() for s in enriched.split("\n") if s.strip()]

        # 如果数量不匹配，回退到原文
        if len(enriched_sentences) != len(sentences):
            logger.warning(
                f"[TextEnricher] 标注后句子数不匹配: "
                f"输入={len(sentences)}, 输出={len(enriched_sentences)}，使用原文"
            )
            return sentences

        return enriched_sentences

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
