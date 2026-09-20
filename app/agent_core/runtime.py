"""v7 runtime 入口（基于 LangGraph StateGraph + AsyncPostgresSaver checkpointer）。

run_skill_graph 是对外的统一入口：
- mode='save'：跑 intent_extract → skill_generate（用于编辑器即时保存）
- mode='publish'：跑全 7 节点（intent → risk → generate → adapter → sandbox → publish → xmodel）

NODE_SEQUENCE / SAVE_NODES 仍然导出供 router/streaming 引用，
保持向后兼容（旧代码可以仍然按节点列表迭代）。
"""

from __future__ import annotations

from uuid import uuid4

from app.agent_core.graph import (
    SkillRuntimeState,
    get_publish_graph,
    get_save_graph,
)
from app.agent_core.nodes.adapter_bind import adapter_bind_node
from app.agent_core.nodes.cross_model_check import cross_model_check_node
from app.agent_core.nodes.intent_extract import intent_extract_node
from app.agent_core.nodes.publish_gate import publish_gate_node
from app.agent_core.nodes.risk_gate import risk_gate_node
from app.agent_core.nodes.sandbox_run import sandbox_run_node
from app.agent_core.nodes.skill_generate import skill_generate_node


NODE_SEQUENCE = [
    ("intent_extract", intent_extract_node),
    ("risk_gate", risk_gate_node),
    ("skill_generate", skill_generate_node),
    ("adapter_bind", adapter_bind_node),
    ("sandbox_run", sandbox_run_node),
    ("publish_gate", publish_gate_node),
    ("cross_model_check", cross_model_check_node),
]

SAVE_NODES = {"intent_extract", "skill_generate"}


async def run_skill_graph(
    user_input: str,
    mode: str = "publish",
    *,
    thread_id: str | None = None,
) -> SkillRuntimeState:
    """跑 v7 graph 并返回最终 state。

    mode='save'   → 编辑器即时保存路径，只跑 intent_extract + skill_generate
    mode='publish' → 完整 7 节点链路（含 cross_model_check）

    thread_id 用于 langgraph checkpoint 持久化；不传则生成临时 id。
    interrupt 恢复请用 resume_skill_graph(thread_id, ...).
    """
    graph = await (get_save_graph() if mode == "save" else get_publish_graph())
    config = {"configurable": {"thread_id": thread_id or f"adhoc-{uuid4().hex[:8]}"}}
    result = await graph.ainvoke({"user_input": user_input}, config=config)
    return result


async def resume_skill_graph(
    thread_id: str,
    *,
    mode: str = "publish",
    resume_value: dict | None = None,
) -> SkillRuntimeState:
    """从 interrupt 中断点恢复执行（D3 任务用）。"""
    from langgraph.types import Command

    graph = await (get_save_graph() if mode == "save" else get_publish_graph())
    config = {"configurable": {"thread_id": thread_id}}
    cmd = Command(resume=resume_value or {})
    result = await graph.ainvoke(cmd, config=config)
    return result
