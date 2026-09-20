"""
AI从周报生成SKILL.md服务。
从 service.py 拆分而来，专注于LLM驱动的Skill生成逻辑。

v1.8.3 起注入 4 阶段方法论 (Phase 0 triage + Phase 1 11 视角)，
让 LLM 在分析周报时也用 11 视角系统化思考，而不是临时提取条件。
"""

import json

import httpx
from loguru import logger

from app.common.exceptions import AppError
from app.config import settings
from app.skills.intelligence.methodology import PHASE_0_TRIAGE_PROMPT, render_phase_1_prompt
from app.skills.core.parser import (
    Antipattern,
    Branch,
    DecisionStep,
    SkillStructured,
    skill_parser,
)

# 周报分析提示词模板 (注入 4 阶段方法论)
_REPORT_ANALYSIS_PROMPT = (
    """你是 SkillForge 的 AI Skill 设计师。SkillForge 是企业内部的 Skill 协作平台,
所有 Skill 跑在 OpenClaw / AIClaw 上, 由业务方在 Web 端编辑 + 审批,
执行结果通过钉钉互动卡片推送给个人执行人。

你的任务是从用户上传的运营周报里, 提取出可自动化的决策 Skill。

"""
    + PHASE_0_TRIAGE_PROMPT
    + "\n"
    + render_phase_1_prompt()
    + """

周报内容:
{report_content}

部门: {department}
岗位: {role}

请按以下 JSON Schema 输出 (在 JSON 之外不要写任何内容):
{{
  "triage_decision": "USE_EXISTING / IMPROVE / CREATE_NEW / COMPOSE 之一",
  "triage_reason": "1-2 句话, 解释为什么是这个分类",
  "name": "Skill 名称 (中文显示名)",
  "purpose": "一句话描述这个 Skill 的目的, 参考 Root Cause + First Principles 视角",
  "steps": [
    {{"name": "步骤名", "branches": [
      {{"condition": "判断条件", "conclusion": "结论", "action": "建议动作"}}
    ]}}
  ],
  "params": [
    {{"name": "参数名", "default_value": "默认值", "description": "说明"}}
  ],
  "antipatterns": [
    {{"scenario": "误判场景 (Inversion / Pre-Mortem 视角)", "correct_action": "正确做法"}}
  ],
  "constraints": [
    "关键约束条目 (Constraints 视角, 例如数据可获取性 / 钉钉 API 限频 / 合规)"
  ],
  "upstream_downstream": {{
    "upstream": ["上游依赖 (Systems Thinking 视角, 数据源 / 其他 Skill)"],
    "downstream": ["下游影响 (Second-Order 视角, 触发的后续流程)"]
  }}
}}

严格要求:
1. antipatterns 至少 2 条 (否则说明你没用 Inversion / Pre-Mortem 视角)
2. constraints 至少 2 条 (否则说明你没用 Constraints 视角)
3. 如果 triage_decision 是 USE_EXISTING / IMPROVE / COMPOSE, purpose 字段必须以
   "[Phase 0 建议: XXX] " 开头, 让审核人立即看到不建议新建的提示
4. 只输出 JSON, 不要输出其他内容"""
)


def _strip_code_fences(text: str) -> str:
    """去除LLM返回中可能包含的markdown代码围栏"""
    text = text.strip()
    # 去掉 ```json ... ``` 或 ``` ... ```
    if text.startswith("```"):
        # 去掉第一行（```json 或 ```）
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


async def generate_from_report(
    report_content: str,
    department: str,
    role: str,
) -> dict:
    """
    上传运营周报 → LLM提取决策逻辑 → 生成SKILL.md初稿。

    1. 构建prompt让LLM分析周报，提取：
       - 高频判断模式（作为执行步骤）
       - 决策阈值（作为参数）
       - 常见异常（作为反例）
    2. 调用 LLM 生成结构化输出
    3. 组装为SKILL.md格式
    4. 返回 {skill_md_draft, extracted_params, extracted_antipatterns}
    """
    # 构建提示词
    # 防止提示注入：用JSON转义用户输入
    safe_content = json.dumps(report_content, ensure_ascii=False)
    prompt = _REPORT_ANALYSIS_PROMPT.format(
        report_content=safe_content,
        department=json.dumps(department or "未指定", ensure_ascii=False),
        role=json.dumps(role or "未指定", ensure_ascii=False),
    )

    # 调用AI模型（配置从 system_config DB 表读取）
    from app.common.ai import get_ai_config
    config = await get_ai_config()
    api_base = str(config["ai.api_base"]).rstrip("/")
    url = f"{api_base}/chat/completions"
    headers = {"Content-Type": "application/json"}
    api_key = str(config.get("ai.api_key", ""))
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": str(config["ai.model"]),
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code != 200:
            error_detail = resp.text[:500]
            logger.error(f"LLM 返回错误 status={resp.status_code}: {error_detail}")
            raise AppError("LLM_API_ERROR", 502, {"detail": error_detail})

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise AppError("LLM_API_ERROR", 502, {"detail": "LLM返回空choices"})

        raw_content = choices[0].get("message", {}).get("content", "")
        if not raw_content:
            raise AppError("LLM_API_ERROR", 502, {"detail": "LLM未返回内容"})

    except httpx.TimeoutException:
        logger.error("周报分析LLM请求超时")
        raise AppError("LLM_TIMEOUT", 504)
    except httpx.ConnectError as e:
        logger.error(f"无法连接模型网关: {e}")
        raise AppError("LLM_API_ERROR", 502,
                        {"detail": f"无法连接模型网关: {settings.AI_API_BASE}"})
    except AppError:
        raise
    except Exception as e:
        logger.error(f"LLM 调用异常: {e}")
        raise AppError("LLM_API_ERROR", 502, {"detail": str(e)})

    # 解析LLM返回的JSON（可能被markdown代码围栏包裹）
    cleaned = _strip_code_fences(raw_content)
    try:
        llm_result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"LLM返回的JSON解析失败: {e}, 原文: {cleaned[:300]}")
        raise AppError("LLM_API_ERROR", 502,
                        {"detail": "LLM返回的内容不是合法JSON，请重试"})

    # Phase 0 triage 决定记录到日志, 并在 purpose 中注入提醒
    triage_decision = str(llm_result.get("triage_decision", "CREATE_NEW")).strip().upper()
    triage_reason = str(llm_result.get("triage_reason", "")).strip()
    if triage_decision in {"USE_EXISTING", "IMPROVE", "COMPOSE"}:
        logger.warning(
            "周报生成 Skill Phase 0 triage 建议 {}: {}",
            triage_decision, triage_reason,
        )
    else:
        logger.info(
            "周报生成 Skill Phase 0 triage = CREATE_NEW: {}",
            triage_reason or "无说明",
        )

    # 将LLM结果组装为SkillStructured，再用parser渲染为SKILL.md
    skill_name = llm_result.get("name", "未命名Skill")
    purpose = llm_result.get("purpose", "")

    # triage 不是 CREATE_NEW 时, 在 purpose 头部强制注入提醒 (即使 LLM 忘了加)
    if triage_decision != "CREATE_NEW" and not purpose.startswith("[Phase 0 建议:"):
        purpose = f"[Phase 0 建议: {triage_decision}] {purpose}"

    # 构建步骤
    steps = []
    for idx, step_data in enumerate(llm_result.get("steps", []), 1):
        branches = []
        for branch_data in step_data.get("branches", []):
            branches.append(Branch(
                condition=branch_data.get("condition", ""),
                conclusion=branch_data.get("conclusion", ""),
                action=branch_data.get("action", ""),
            ))
        steps.append(DecisionStep(
            id=str(idx),
            name=step_data.get("name", f"步骤{idx}"),
            branches=branches,
        ))

    # 构建反例
    antipatterns = []
    for ap_data in llm_result.get("antipatterns", []):
        antipatterns.append(Antipattern(
            scenario=ap_data.get("scenario", ""),
            correct_action=ap_data.get("correct_action", ""),
        ))

    # 构建 frontmatter
    # 注意: name 字段必须是 Anthropic 标准的 hyphen-case skill_id 而非中文显示名,
    # 这样 quick_validate 才能通过, 中文名放到 description 中
    frontmatter = {
        "name": skill_name,
        "description": (purpose[:500] if purpose else f"{skill_name}。Use when: 周报生成的初稿"),
        "department": department or "未指定",
        "role": role or "未指定",
        "trigger_type": "manual",
        "risk_level": "R2",
    }

    # 组装SkillStructured并渲染
    structured = SkillStructured(
        frontmatter=frontmatter,
        purpose=purpose,
        steps=steps,
        antipatterns=antipatterns,
    )
    skill_md_draft = skill_parser.render(structured)

    # 提取参数列表（给前端展示预览用）
    extracted_params = llm_result.get("params", [])

    # 提取反例列表（给前端展示预览用）
    extracted_antipatterns = [
        {"scenario": ap.get("scenario", ""), "correct_action": ap.get("correct_action", "")}
        for ap in llm_result.get("antipatterns", [])
    ]

    return {
        "skill_md_draft": skill_md_draft,
        "extracted_params": extracted_params,
        "extracted_antipatterns": extracted_antipatterns,
        # Phase 0 triage 信息透传到前端, 让用户知道这是建议新建还是建议复用
        "triage_decision": triage_decision,
        "triage_reason": triage_reason,
        "constraints": llm_result.get("constraints", []),
        "upstream_downstream": llm_result.get("upstream_downstream", {}),
    }
