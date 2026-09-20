"""v7 agent_core 的 LLM 入口。

这里只做"用 LLM 抽取意图，再走标准后处理生成 bundle"这一件事，
具体的 LLM 调用与 fallback 在 intent_extractor.py 中实现。
"""

from __future__ import annotations

from app.agent_core.intent_extractor import extract_contract
from app.workbench.task_contract import build_bundle_from_contract


async def generate_contract_bundle(message: str) -> dict:
    """业务自然语言 → 完整 v7 任务合同 bundle。

    内部步骤：
    1. extract_contract: LLM 抽取严格 contract（失败则正则 fallback）
    2. build_bundle_from_contract: 渲染 intent_md / preview / gate / skill 等
    """
    contract = await extract_contract(message)
    return build_bundle_from_contract(message, contract)
