"""v7 LangGraph StateGraph 定义（save / publish 两套图）。

- save 模式：intent_extract → skill_generate → END（用于编辑器即时保存）
- publish 模式：6 节点完整链路 + 第 7 节点 cross_model_check（用于审核/发布前体检）

risk_gate / publish_gate 内部用 langgraph.types.interrupt() 实现人工确认中断。
"""

from __future__ import annotations

from typing import Optional, TypedDict

from app.agent_core.checkpointer import get_checkpointer
from app.agent_core.nodes.adapter_bind import adapter_bind_node
from app.agent_core.nodes.intent_extract import intent_extract_node
from app.agent_core.nodes.publish_gate import publish_gate_node
from app.agent_core.nodes.risk_gate import risk_gate_node
from app.agent_core.nodes.sandbox_run import sandbox_run_node
from app.agent_core.nodes.skill_generate import skill_generate_node


class SkillRuntimeState(TypedDict, total=False):
    user_input: str
    intent_md: str
    contract: dict
    skill: dict
    skill_md: str
    skill_md_llm: str
    policy_yaml: str
    adapter: dict
    preview_result: dict
    review_state: dict
    gate: dict
    preview_cache_key: str
    xmodel_check: dict


_save_graph = None
_publish_graph = None


async def _build_save_graph(checkpointer):
    from langgraph.graph import StateGraph, START, END

    builder = StateGraph(SkillRuntimeState)
    builder.add_node("intent_extract", intent_extract_node)
    builder.add_node("skill_generate", skill_generate_node)
    builder.add_edge(START, "intent_extract")
    builder.add_edge("intent_extract", "skill_generate")
    builder.add_edge("skill_generate", END)
    return builder.compile(checkpointer=checkpointer)


async def _build_publish_graph(checkpointer):
    from langgraph.graph import StateGraph, START, END

    # 延迟导入 cross_model_check 避免循环（D4 任务实现）
    from app.agent_core.nodes.cross_model_check import cross_model_check_node

    builder = StateGraph(SkillRuntimeState)
    builder.add_node("intent_extract", intent_extract_node)
    builder.add_node("risk_gate", risk_gate_node)
    builder.add_node("skill_generate", skill_generate_node)
    builder.add_node("adapter_bind", adapter_bind_node)
    builder.add_node("sandbox_run", sandbox_run_node)
    builder.add_node("publish_gate", publish_gate_node)
    builder.add_node("cross_model_check", cross_model_check_node)

    builder.add_edge(START, "intent_extract")
    builder.add_edge("intent_extract", "risk_gate")
    builder.add_edge("risk_gate", "skill_generate")
    builder.add_edge("skill_generate", "adapter_bind")
    builder.add_edge("adapter_bind", "sandbox_run")
    builder.add_edge("sandbox_run", "publish_gate")
    builder.add_edge("publish_gate", "cross_model_check")
    builder.add_edge("cross_model_check", END)
    return builder.compile(checkpointer=checkpointer)


async def get_save_graph():
    global _save_graph
    if _save_graph is None:
        checkpointer = await get_checkpointer()
        _save_graph = await _build_save_graph(checkpointer)
    return _save_graph


async def get_publish_graph():
    global _publish_graph
    if _publish_graph is None:
        checkpointer = await get_checkpointer()
        _publish_graph = await _build_publish_graph(checkpointer)
    return _publish_graph


def reset_graphs() -> None:
    """供测试用：清空已编译的图，强制下次重新编译（例如换 checkpointer）。"""
    global _save_graph, _publish_graph
    _save_graph = None
    _publish_graph = None
