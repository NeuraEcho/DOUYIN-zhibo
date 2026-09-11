"""
商品问答知识库 - 仅供「观众问答」链路使用（播报/口播稿不受影响）

设计：
- 单例，进程内共享；持久化到 data/product_knowledge.json，重启不丢。
- product_info：商品权威资料（名称/价格/卖点/规格/优惠/库存等自由文本）。
- examples：多条「问答示例」(question/answer)，作为 few-shot 供大模型参考，
  要求 LLM 按示例的内容与口吻回答同类问题。
- build_qa_context()：把商品信息 + 示例拼成一段系统提示词片段，注入问答链路
  （prompt_assembler 的 DANMAKU_REPLY 分支 与 qa_inserter 的预生成）。
  两者都为空时返回 ""，问答链路行为与之前完全一致（不注入）。
"""

import json
from pathlib import Path
from typing import Optional

from loguru import logger

_DB_PATH = Path("./data/product_knowledge.json")


class ProductKnowledge:
    """商品问答知识库（单例，持久化到 data/product_knowledge.json）"""

    _instance: Optional["ProductKnowledge"] = None

    def __init__(self):
        self._product_info: str = ""
        self._examples: list = []   # [{"question": str, "answer": str}, ...]
        self._load()

    @classmethod
    def get_instance(cls) -> "ProductKnowledge":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---------- 本地持久化 ----------
    def _load(self):
        if _DB_PATH.exists():
            try:
                data = json.loads(_DB_PATH.read_text(encoding="utf-8"))
                self._product_info = (data.get("product_info") or "").strip()
                self._examples = self._clean_examples(data.get("examples") or [])
                logger.info(
                    f"[ProductKnowledge] 已加载商品资料: 信息长度={len(self._product_info)}, "
                    f"示例数={len(self._examples)}"
                )
            except Exception as e:
                logger.warning(f"[ProductKnowledge] 加载商品资料失败: {e}")

    def _save(self):
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"product_info": self._product_info, "examples": self._examples}
            _DB_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"[ProductKnowledge] 持久化商品资料失败: {e}")

    @staticmethod
    def _clean_examples(raw: list) -> list:
        """规范化示例列表：只保留 question/answer 均为非空字符串的项"""
        cleaned = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            a = str(item.get("answer") or "").strip()
            if q and a:
                cleaned.append({"question": q, "answer": a})
        return cleaned

    # ---------- 读取 ----------
    def get_product_info(self) -> str:
        return self._product_info

    def get_examples(self) -> list:
        return [dict(ex) for ex in self._examples]

    def get_data(self) -> dict:
        return {"product_info": self._product_info, "examples": self.get_examples()}

    # ---------- 更新（前端配置面板调用）----------
    def update(self, product_info: Optional[str] = None, examples: Optional[list] = None):
        """整体更新商品资料并持久化；仅更新传入的非 None 项"""
        if product_info is not None:
            self._product_info = (product_info or "").strip()
        if examples is not None:
            self._examples = self._clean_examples(examples)
        self._save()
        logger.info(
            f"[ProductKnowledge] 商品资料已更新: 信息长度={len(self._product_info)}, "
            f"示例数={len(self._examples)}"
        )

    # ---------- 组装问答提示词片段 ----------
    def build_qa_context(self) -> str:
        """
        拼出注入问答链路的系统提示词片段（商品信息 + 参考示例）。
        都为空时返回 ""（问答链路不注入，行为与之前一致）。
        """
        blocks = []

        if self._product_info:
            blocks.append(
                "【商品信息】以下是本直播间商品的权威资料，是你回答观众提问的唯一事实依据。"
                "只能依据这些信息作答，禁止编造其中没有的价格、规格、材质、库存、优惠等内容；"
                "资料里没有提到的，就如实说这个暂时不清楚，引导观众看商品详情页或稍后咨询。\n"
                f"{self._product_info.strip()}"
            )

        if self._examples:
            lines = [
                "【参考问答示例】请严格模仿以下示例的内容、口吻、详略和回答方式来回答观众提问；"
                "如果观众的问题与某条示例相近，直接按那条示例的答法回答，不要自由发挥："
            ]
            for i, ex in enumerate(self._examples, 1):
                lines.append(f"示例{i}")
                lines.append(f"问：{ex['question']}")
                lines.append(f"答：{ex['answer']}")
            blocks.append("\n".join(lines))

        return "\n\n".join(blocks)
