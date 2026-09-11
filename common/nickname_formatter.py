"""
观众昵称格式化工具 - 把直播间观众昵称统一格式化为「xx姐姐」称呼

供两处共用：
- 弹幕回答带昵称（AI 回答观众提问时先自然地叫一声「xx姐姐」）
- 口播稿随机点名（{点名} 占位符替换为最近互动观众的格式化昵称）

格式化规则（按用户口述）：
- 空 / None              → "用户姐姐"（兜底昵称「用户」）
- 表情（emoji）开头       → "这位表情姐姐"
- 英文字母开头            → "这位英文姐姐"
- 数字 / 特殊符号开头     → "这位符号姐姐"
- 中文开头：过长只取前 2 位，否则原样 → "{name}姐姐"
"""

# 中文昵称超过此长度视为「过长」
_MAX_CN_LEN = 4
# 过长时只保留前 N 位
_KEEP_LEN = 2
# 空昵称兜底
_FALLBACK_NAME = "用户"
# 统一称呼后缀
_SUFFIX = "姐姐"


def _char_kind(ch: str) -> str:
    """判断单个字符类型：empty / emoji / en / cn / digit / symbol"""
    if not ch:
        return "empty"
    cp = ord(ch)
    # 表情 / 图形符号（覆盖常见 emoji Unicode 区块）
    if (
        (0x1F000 <= cp <= 0x1FAFF)   # 各类象形文字/表情/符号
        or (0x2600 <= cp <= 0x27BF)  # 杂项符号 + 装饰符号（☀ ✅ ✨ …）
        or (0x2B00 <= cp <= 0x2BFF)  # 杂项符号与箭头（⭐ …）
        or (0x2190 <= cp <= 0x21FF)  # 箭头
        or (0xFE00 <= cp <= 0xFE0F)  # 变体选择符
        or cp in (0x200D, 0x20E3, 0x3030, 0x303D, 0x3297, 0x3299)
    ):
        return "emoji"
    # 英文字母
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
        return "en"
    # 中文（CJK 统一表意文字基本区 + 扩展 A）
    if (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF):
        return "cn"
    # 数字
    if ch.isdigit():
        return "digit"
    # 其余（标点、特殊符号等）
    return "symbol"


def format_viewer_nickname(raw: str) -> str:
    """把观众原始昵称格式化为「xx姐姐」称呼。详见模块 docstring 的规则。"""
    name = (raw or "").strip()
    if not name:
        return f"{_FALLBACK_NAME}{_SUFFIX}"

    kind = _char_kind(name[0])
    if kind == "emoji":
        return f"这位表情{_SUFFIX}"
    if kind == "en":
        return f"这位英文{_SUFFIX}"
    if kind in ("digit", "symbol"):
        return f"这位符号{_SUFFIX}"

    # 中文开头：提取开头连续中文（避免 TTS 朗读夹杂的符号/表情），过长则只取前 2 位
    cn_prefix = ""
    for ch in name:
        if _char_kind(ch) == "cn":
            cn_prefix += ch
        else:
            break
    if len(name) > _MAX_CN_LEN:
        cn_prefix = cn_prefix[:_KEEP_LEN]
    if not cn_prefix:
        cn_prefix = _FALLBACK_NAME
    return f"{cn_prefix}{_SUFFIX}"
