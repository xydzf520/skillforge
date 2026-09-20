"""工作台意图识别。

主路径:LLM 分类(detect_intent_llm) — 准、能识别同义词、能结合 Skill 上下文。
降级:关键词规则(detect_intent) — LLM 失败/超时时兜底,也用于离线测试。
"""

from app.workbench.schemas import ModuleName

# 「跑 Skill」类动词/用法：用户想直接执行当前 Skill 拿结果，
# 不是想改 Skill 也不是问问题。命中 → intent="run_skill"
# 注意：必须排在 _QUESTION_VERBS 之前判断（"分析昨天" 既像 question 也像 run，
# 但有具体时间/数量词时优先走 run）
_RUN_KEYWORDS = (
    "运行", "执行", "跑一下", "跑下", "跑跑", "试跑", "试运行", "调用",
    "查一下", "查下", "帮我查", "查询",
    "统计", "算一下", "算下", "数一下", "数下",
    "拉一下", "拉取", "去拉", "去查",
    "昨天", "今天", "上周", "本周", "上月", "本月", "最近",
    "多少条", "多少个", "几条", "几个",
)

# 「编辑 Skill」类问答动词：命中 → 走 question
_QUESTION_VERBS = (
    "提取", "解释", "解读", "总结", "归纳", "列出", "列举",
    "有哪些", "是什么", "为什么", "怎么", "如何", "检查", "评估",
    "看看", "给我看", "告诉我", "说明", "介绍", "查看", "帮我看",
    "explain", "list", "show", "what", "why", "how",
)

_MODULE_KEYWORDS: list[tuple[ModuleName, tuple[str, ...], str]] = [
    ("params", ("参数", "阈值", "roi", "预算", "系数", "调高", "调低"), "tune_threshold"),
    ("rules", ("规则", "逻辑", "判断", "条件", "步骤"), "rewrite_module"),
    ("output_table", ("输出", "表格", "字段", "列", "展示"), "rewrite_module"),
    ("test_cases", ("测试", "样例", "case", "回放"), "rewrite_module"),
    ("workflow", ("工作流", "流程", "节点", "编排", "审批链"), "insert_workflow_node"),
    ("goal", ("目标", "目的", "说明", "定位"), "rewrite_module"),
]


def detect_intent(message: str, active_module: ModuleName | None = None) -> dict:
    text = (message or "").strip()
    lower = text.lower()

    if not text:
        return {
            "target_module": active_module or "goal",
            "intent": "question",
            "confidence": 0.0,
            "need_clarification": True,
            "clarification_question": "你希望先改目标、规则、参数、输出表格、测试样例，还是工作流？",
            "reasoning": "用户未提供有效输入，需要先澄清目标模块。",
        }

    # 优先级 1：「跑 Skill」类 — 命中则走 run_skill 直接调远端执行
    if any(kw in lower for kw in _RUN_KEYWORDS):
        return {
            "target_module": active_module or "goal",
            "intent": "run_skill",
            "confidence": 0.85,
            "need_clarification": False,
            "clarification_question": None,
            "reasoning": "检测到执行/查询/时间词，走 run_skill 模式调用远端执行。",
        }

    # 优先级 2：问答/分析动词 — 命中则直接走 question，不做 patch
    if any(verb in lower for verb in _QUESTION_VERBS):
        # 尝试推断涉及哪个模块（用于提供上下文）
        for module_name, keywords, _ in _MODULE_KEYWORDS:
            if any(kw in lower for kw in keywords):
                return {
                    "target_module": module_name,
                    "intent": "question",
                    "confidence": 0.90,
                    "need_clarification": False,
                    "clarification_question": None,
                    "reasoning": f"检测到分析/问答动词，走问答模式（模块={module_name}）。",
                }
        return {
            "target_module": active_module or "goal",
            "intent": "question",
            "confidence": 0.85,
            "need_clarification": False,
            "clarification_question": None,
            "reasoning": "检测到分析/问答动词，走问答模式。",
        }

    matches: list[tuple[ModuleName, str]] = []
    for module_name, keywords, intent in _MODULE_KEYWORDS:
        if any(keyword in lower for keyword in keywords):
            matches.append((module_name, intent))

    if len(matches) == 1:
        module_name, intent = matches[0]
        return {
            "target_module": module_name,
            "intent": intent,
            "confidence": 0.92,
            "need_clarification": False,
            "clarification_question": None,
            "reasoning": f"基于关键词匹配，推断模块={module_name}，意图={intent}。",
        }

    if len(matches) > 1:
        # 多模块命中且无明确修改动词 → 走问答，让 AI 综合分析
        return {
            "target_module": active_module or "goal",
            "intent": "question",
            "confidence": 0.60,
            "need_clarification": False,
            "clarification_question": None,
            "reasoning": f"命中了多个模块关键词（{[m for m, _ in matches]}），无明确修改动词，走问答模式。",
        }

    fallback_module = active_module or "goal"
    return {
        "target_module": fallback_module,
        "intent": "question",
        "confidence": 0.35,
        "need_clarification": False,
        "clarification_question": None,
        "reasoning": "未命中明确模块关键词，回退到问答模式。",
    }


_VALID_PATCH_KINDS = {
    "tune_threshold", "add_rule", "modify_rule", "rewrite_selection",
    "add_output", "add_test_case", "modify_workflow",
    "generate_tests", "suggest_branches",
}


async def detect_intent_llm(
    message: str,
    skill_meta: dict | None = None,
    active_module: ModuleName | None = None,
) -> dict | None:
    """LLM 意图分类。失败时返回 None,由调用方降级到 detect_intent (关键词版)。

    输出 dict:
    {
      "intent": "run_skill" | "question" | <patch_kind>,
      "target_module": "...",
      "confidence": 0.0-1.0,
      "reasoning": "..."
    }
    """
    from app.common.ai import call_llm
    from loguru import logger

    text = (message or "").strip()
    if not text:
        return None

    skill_name = (skill_meta or {}).get("name") or "当前 Skill"
    skill_desc = (skill_meta or {}).get("description") or ""
    module_hint = f"\n当前激活模块: {active_module}" if active_module else ""

    system = f"""你是 SkillForge Skill 编辑器的意图分类器。
当前用户正在编辑 Skill: {skill_name}
Skill 描述: {skill_desc}{module_hint}

把用户消息分到下列三类之一,严格输出 JSON,不要解释:

A. **run_skill** — 用户想"直接执行这个 Skill"拿业务运行时数据。
   特征: 问"昨天/今天/上周 ... 多少 / 哪些 / 平均 / 排名"等真实业务问题、
        说"运行/跑/试一下/查/统计/分析数据/拉一下"。
   例: "昨天有多少投诉对话"、"分析下本周流量"、"统计平台分布"、"运行下"

B. **question** — 用户问的是这个 Skill 本身的元问题(目标/逻辑/规则有哪些/如何工作)。
   特征: 主语是"这个 Skill / 它"、问"为什么/如何/有几个"。
   例: "这个 Skill 的目标是什么"、"解释决策逻辑"、"有几个测试用例"、"它怎么判断 R3 风险"

C. **patch_xxx** — 用户想改这个 Skill 的 SKILL.md。返回具体 patch_kind:
   - tune_threshold (调整数值阈值/参数)
   - add_rule (新增规则)
   - modify_rule (修改已有规则)
   - rewrite_selection (重写当前选中的文本片段)
   - add_output (加输出字段)
   - add_test_case (加测试用例)
   - modify_workflow (改工作流节点)
   - generate_tests (要求 AI 自动生成测试用例)
   - suggest_branches (要求 AI 补全规则的异常分支)

输出格式 (严格 JSON):
{{"intent": "run_skill", "target_module": "goal", "confidence": 0.9, "reasoning": "..."}}
或
{{"intent": "tune_threshold", "target_module": "params", "confidence": 0.9, "reasoning": "..."}}
"""

    try:
        result = await call_llm(
            system=system,
            user=text,
            json_mode=True,
            temperature=0.1,
            max_tokens=200,
            call_source="workbench.intent_classify",
        )
    except Exception as e:
        logger.warning("[intent_llm] call_llm 异常: {}", e)
        return None

    if not isinstance(result, dict):
        return None
    intent = result.get("intent")
    if intent not in {"run_skill", "question"} and intent not in _VALID_PATCH_KINDS:
        logger.warning("[intent_llm] 返回未知 intent={}, 降级", intent)
        return None

    return {
        "target_module": result.get("target_module") or active_module or "goal",
        "intent": intent,
        "confidence": float(result.get("confidence") or 0.8),
        "need_clarification": False,
        "clarification_question": None,
        "reasoning": result.get("reasoning") or "LLM 分类",
    }
