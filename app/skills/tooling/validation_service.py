"""
Skill校验与参数影响预览服务。
从 service.py 拆分而来，专注于校验和参数对比逻辑。
"""

import json
import re

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt
from app.execution.models import DecisionLog
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser
from app.skills.core.service_shared import validate_skill_id


def _read_contract_for_validation(skill_id: str) -> dict:
    raw = git_service.read_file(skill_id, "contract.json")
    if not raw:
        return {}
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return contract if isinstance(contract, dict) else {}


def _list_skill_files_for_validation(skill_id: str) -> list[str]:
    try:
        files = git_service.list_skill_files(skill_id)
    except Exception:
        return []
    return files if isinstance(files, list) else []


def _has_param_definition(parsed, contract: dict, skillforge_yaml_exists: bool) -> tuple[bool, str]:
    fm = parsed.frontmatter if parsed else {}
    fm_params = fm.get("params") if isinstance(fm, dict) else None
    contract_inputs = contract.get("input") or contract.get("inputs")
    input_schema = contract.get("input_schema") or contract.get("params_schema")
    if fm_params:
        return True, "使用 SKILL.md frontmatter params 定义参数"
    if isinstance(contract_inputs, list) and contract_inputs:
        return True, "使用 contract.json input 定义参数"
    if isinstance(input_schema, dict) and input_schema.get("properties"):
        return True, "使用 contract.json input_schema 定义参数"
    if skillforge_yaml_exists:
        return True, "使用 skillforge.yaml 定义参数/运行配置"
    return False, ""


def _has_markdown_decision_logic(skill_md: str, contract: dict) -> tuple[bool, str]:
    rules = contract.get("rules") or contract.get("workflow") or contract.get("decision_steps")
    if isinstance(rules, list) and rules:
        return True, f"contract.json 定义了 {len(rules)} 个规则/步骤"
    if isinstance(rules, dict) and rules:
        return True, "contract.json 定义了工作流/规则"

    has_decision_section = bool(re.search(r"^##\s*(执行步骤|决策阶梯|判断逻辑|分析框架)\b", skill_md, re.M))
    step_headers = re.findall(r"^#{2,4}\s*(?:step[_\s-]*\d+|步骤\s*\d+|阶段\s*\d+)[:：]?", skill_md, re.I | re.M)
    decision_table = bool(re.search(r"^\|\s*条件\s*\|\s*结论\s*\|\s*动作\s*\|", skill_md, re.M))
    has_next_step = bool(re.search(r"^\|\s*.*下一步.*\|", skill_md, re.M))
    if has_decision_section and (step_headers or decision_table or has_next_step):
        count = len(step_headers) or 1
        return True, f"Markdown 决策逻辑已定义（{count} 个步骤/阶梯）"
    return False, ""


def _has_external_test_assets(skill_id: str) -> tuple[bool, str]:
    files = _list_skill_files_for_validation(skill_id)
    fixture_files = [
        path for path in files
        if path.startswith("fixtures/") and path.endswith((".json", ".yaml", ".yml", ".ndjson", ".txt"))
    ]
    test_files = [
        path for path in files
        if path.startswith("tests/") and path.endswith((".py", ".ts", ".js", ".mjs", ".cjs"))
    ]
    if test_files and fixture_files:
        return True, f"检测到 {len(test_files)} 个测试文件和 {len(fixture_files)} 个 fixture"
    if test_files:
        return True, f"检测到 {len(test_files)} 个测试文件"
    if fixture_files:
        return True, f"检测到 {len(fixture_files)} 个 fixture，可用于样例测试"
    return False, ""


async def validate_block(db: AsyncSession, skill_id: str, block_type: str, content: dict) -> dict:
    """
    逐块校验：单独校验一个Block的内容，返回错误/警告/覆盖率信息。
    支持的 block_type: frontmatter / purpose / steps / params / antipatterns /
                       output_definition / data_inputs / test_cases
    """
    errors = []
    warnings = []
    coverage = {}

    if block_type == "frontmatter":
        missing = []
        for key in ("name", "department", "trigger_type"):
            if not content.get(key):
                missing.append(key)
        if missing:
            errors.append(f"缺少必填字段: {', '.join(missing)}")
        # 校验 department 合法值
        valid_depts = {"EC", "SEM", "社媒", "品牌", "CRM", "公共"}
        dept = content.get("department", "")
        if dept and dept not in valid_depts:
            warnings.append(f"部门 '{dept}' 不在常用列表中: {', '.join(sorted(valid_depts))}")
        # 校验 cron 表达式语法
        trigger_expr = content.get("trigger_expression", "")
        if content.get("trigger_type") == "cron" and trigger_expr:
            parts = trigger_expr.strip().split()
            if len(parts) != 5:
                errors.append(f"cron 表达式格式错误: 应为5段，当前{len(parts)}段")

    elif block_type == "purpose":
        text = content.get("text", "") if isinstance(content, dict) else str(content)
        if not text.strip():
            errors.append("缺少目的描述")
        elif len(text.strip()) < 20:
            warnings.append(f"目的描述过短: 当前{len(text.strip())}字符，建议至少20字符")

    elif block_type == "steps":
        steps_list = content if isinstance(content, list) else content.get("steps", [])
        if not steps_list:
            errors.append("缺少判断逻辑，至少需要1个步骤")
        else:
            step_ids = set()
            total_branches = 0
            for s in steps_list:
                sid = s.get("id", "")
                if sid in step_ids:
                    errors.append(f"步骤ID重复: {sid}")
                step_ids.add(sid)
                branches = s.get("branches", [])
                if not branches:
                    errors.append(f"步骤 {sid} 缺少分支")
                else:
                    total_branches += len(branches)
                    for b in branches:
                        if not b.get("condition", "").strip():
                            errors.append(f"步骤 {sid} 存在空条件分支")
                            break
                # next_step 引用有效性
                for b in branches:
                    ns = b.get("next_step")
                    if ns and ns not in step_ids and ns not in [ss.get("id") for ss in steps_list]:
                        warnings.append(f"步骤 {sid} 分支引用了不存在的 next_step: {ns}")

            # 环检测（递归 DFS，每条路径独立 visited）
            def _has_cycle(sid, visited_path):
                if sid in visited_path:
                    return True
                visited_path = visited_path | {sid}
                for s in steps_list:
                    if s.get("id") == sid:
                        for b in s.get("branches", []):
                            ns = b.get("next_step")
                            if ns and _has_cycle(ns, visited_path):
                                return True
                return False
            for s in steps_list:
                if _has_cycle(s.get("id", ""), set()):
                    errors.append("决策树存在环引用")
                    break

            # 覆盖率估算
            branches_with_conclusion = sum(
                1 for s in steps_list for b in s.get("branches", [])
                if b.get("conclusion", "").strip()
            )
            coverage = {
                "total_steps": len(steps_list),
                "total_branches": total_branches,
                "branch_coverage": round(branches_with_conclusion / total_branches, 2) if total_branches > 0 else 0,
                "uncovered": [
                    f"{s.get('id')}" for s in steps_list
                    for b in s.get("branches", [])
                    if not b.get("conclusion", "").strip()
                ],
            }

    elif block_type == "params":
        items = content if isinstance(content, list) else content.get("params", [])
        names = set()
        if not isinstance(items, list):
            errors.append("params 必须是数组")
        else:
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    errors.append(f"参数 #{idx+1} 必须是对象")
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    errors.append(f"参数 #{idx+1} 缺少 name")
                    continue
                if name in names:
                    errors.append(f"参数名重复: {name}")
                names.add(name)
                if item.get("default_value") is None and item.get("default_value_str") in (None, ""):
                    warnings.append(f"参数 '{name}' 没有默认值")

    elif block_type == "antipatterns":
        items = content if isinstance(content, list) else content.get("antipatterns", [])
        if not items:
            warnings.append("建议至少添加1个反例")
        for i, ap in enumerate(items):
            if not ap.get("scenario", "").strip():
                errors.append(f"反例 #{i+1} 缺少场景描述")

    elif block_type == "output_definition":
        items = content if isinstance(content, list) else content.get("output_definition", [])
        if not items:
            warnings.append("建议至少添加1项输出定义")
        names = set()
        for item in items:
            n = item.get("name", "")
            if n in names:
                errors.append(f"输出字段名重复: {n}")
            names.add(n)

    elif block_type == "todos":
        items = content if isinstance(content, list) else content.get("todos", [])
        if not isinstance(items, list):
            errors.append("todos 必须是数组")
        else:
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    errors.append(f"待办 #{idx+1} 必须是对象")
                    continue
                kind = str(item.get("kind") or "review")
                if kind not in {"review", "dispatch"}:
                    errors.append(f"待办 #{idx+1} kind 必须是 review 或 dispatch")
                if not str(item.get("title") or "").strip():
                    errors.append(f"待办 #{idx+1} 缺少 title")
                if not (item.get("reviewers") or item.get("reviewer_role")):
                    warnings.append(f"待办 #{idx+1} 建议配置 reviewers 或 reviewer_role")
                if kind == "dispatch":
                    tasks = item.get("tasks") or []
                    if not isinstance(tasks, list) or not tasks:
                        errors.append(f"派发待办 #{idx+1} 必须包含 tasks")
                    else:
                        for ti, task in enumerate(tasks):
                            if not isinstance(task, dict) or not str(task.get("content") or "").strip():
                                errors.append(f"派发待办 #{idx+1} 的任务 #{ti+1} 缺少 content")

    elif block_type == "data_inputs":
        items = content if isinstance(content, list) else content.get("data_inputs", [])
        # 数据源存在性检查（需要db）
        if items:
            from app.datasources.models import DataSource
            for di in items:
                source_name = di.get("source", "")
                if source_name:
                    result = await db.execute(
                        select(DataSource).where(
                            (DataSource.id == source_name) | (DataSource.name == source_name)
                        )
                    )
                    if not result.scalar_one_or_none():
                        warnings.append(f"数据源 '{source_name}' 不存在")

    elif block_type == "test_cases":
        items = content if isinstance(content, list) else content.get("test_cases", [])
        if len(items) < 3:
            warnings.append(f"测试用例不足: 当前{len(items)}个，建议至少3个")
        for tc in items:
            if not tc.get("input_data"):
                errors.append(f"测试用例 '{tc.get('name', '未命名')}' 缺少 input_data")
            if not tc.get("expected_output"):
                errors.append(f"测试用例 '{tc.get('name', '未命名')}' 缺少 expected_output")
    else:
        errors.append(f"不支持的 block_type: {block_type}")

    return {
        "block_type": block_type,
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
    }


# L3-C 规则抽屉单条规则后端校验
_VALID_VERDICTS = {"绿灯", "黄灯", "红灯", "通过", "关注", "阻断"}
_COMPARISON_HINT_RE = re.compile(r"[><=≥≤≠!]|大于|小于|等于|超过|低于|高于|介于|不等于|属于|不属于")
_VAR_TOKEN_RE = re.compile(r"[A-Za-z_一-鿿][A-Za-z0-9_一-鿿]*")
_TEMPLATE_VAR_RE = re.compile(r"\{(\w+)\}")


def _detect_rule_cycle(
    steps: list[dict],
    start_step_id: str,
    next_step_id: str | None,
) -> bool:
    """从 start_step_id 出发，假设当前编辑分支 next_step = next_step_id，看是否能回到 start_step_id。"""
    if not next_step_id:
        return False
    if next_step_id == start_step_id:
        return True
    by_id: dict[str, dict] = {}
    for step in steps:
        if isinstance(step, dict) and step.get("id"):
            by_id[step["id"]] = step
    if next_step_id not in by_id:
        return False
    visited: set[str] = set()
    stack: list[str] = [next_step_id]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        if current == start_step_id:
            return True
        visited.add(current)
        step = by_id.get(current)
        if not step:
            continue
        for branch in step.get("branches", []) or []:
            ns = branch.get("next_step") if isinstance(branch, dict) else None
            if ns and ns not in visited:
                stack.append(ns)
    return False


async def validate_rule(
    db: AsyncSession,
    skill_id: str,
    step_id: str,
    condition: str,
    verdict: str,
    action: str,
    next_step: str | None,
) -> dict:
    """L3-C 抽屉单条规则后端校验。

    校验内容：
    - 必填字段（条件、结论）
    - next_step 必须指向决策树已存在的步骤
    - 检测循环引用（rule → ... → 回到 step_id）
    - 条件语法（括号是否成对、变量名是否合法）
    - 提示：缺少比较运算符、引用未定义参数

    返回 {valid, errors, warnings, hints}，每条带 field/message。
    """
    validate_skill_id(skill_id)

    errors: list[dict] = []
    warnings: list[dict] = []
    hints: list[dict] = []

    condition = (condition or "").strip()
    verdict = (verdict or "").strip()
    action = (action or "").strip()
    next_step_id = (next_step or "").strip() or None

    # 读 SKILL.md 拿到当前决策树和参数定义
    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    parsed = skill_parser.parse(skill_md) if skill_md else None
    raw_steps = []
    if parsed:
        for s in parsed.steps or []:
            raw_steps.append({
                "id": s.id,
                "name": s.name,
                "branches": [
                    {
                        "condition": b.condition,
                        "conclusion": b.conclusion,
                        "action": b.action,
                        "next_step": b.next_step,
                    }
                    for b in (s.branches or [])
                ],
            })
    step_ids = {s["id"] for s in raw_steps if s.get("id")}

    # 已知参数：frontmatter.params + policy_pack.yaml
    known_params: set[str] = set()
    if parsed:
        fm = parsed.frontmatter or {}
        fm_params = fm.get("params") if isinstance(fm, dict) else None
        if isinstance(fm_params, dict):
            known_params.update(str(k) for k in fm_params.keys())
        elif isinstance(fm_params, list):
            for item in fm_params:
                if isinstance(item, dict) and item.get("name"):
                    known_params.add(str(item["name"]))
    policy_pack_raw = git_service.read_file(skill_id, "policy_pack.yaml")
    if policy_pack_raw:
        try:
            pp = yaml.safe_load(policy_pack_raw) or {}
            if isinstance(pp, dict):
                known_params.update(str(k) for k in pp.keys())
        except yaml.YAMLError:
            pass

    # 1) 必填字段
    if not condition:
        errors.append({"field": "condition", "message": "条件不能为空"})
    if not verdict:
        errors.append({"field": "verdict", "message": "结论不能为空"})
    elif verdict not in _VALID_VERDICTS:
        warnings.append({
            "field": "verdict",
            "message": f"结论 '{verdict}' 不在常用值（绿灯/黄灯/红灯）",
        })
    if not action:
        warnings.append({"field": "action", "message": "建议补充动作描述，便于执行落地"})

    # 2) next_step 自引用 + 存在性
    if next_step_id:
        if next_step_id == step_id:
            errors.append({
                "field": "next_step",
                "message": "下一步不能指向当前步骤（自引用）",
            })
        elif step_ids and next_step_id not in step_ids:
            errors.append({
                "field": "next_step",
                "message": f"下一步 '{next_step_id}' 不存在于当前决策树",
            })
        else:
            # 3) 循环引用：临时把当前编辑的分支 next_step 替换进去再 DFS
            patched_steps = []
            for s in raw_steps:
                if s.get("id") == step_id:
                    branches = list(s.get("branches") or [])
                    # 不知道编辑的是第几条 branch，但只要检查“从 step_id 出发能否到 step_id”
                    # 把当前 next_step_id 作为 step_id 的一个候选 next 即可
                    branches.append({"next_step": next_step_id})
                    patched_steps.append({**s, "branches": branches})
                else:
                    patched_steps.append(s)
            if _detect_rule_cycle(patched_steps, step_id, next_step_id):
                errors.append({
                    "field": "next_step",
                    "message": f"检测到循环引用：从 {step_id} 出发会回到自身",
                })

    # 4) 条件语法
    if condition:
        # 括号成对
        if condition.count("(") != condition.count(")"):
            errors.append({"field": "condition", "message": "条件括号 '(' 与 ')' 数量不匹配"})
        if condition.count("（") != condition.count("）"):
            errors.append({"field": "condition", "message": "条件全角括号 '（' 与 '）' 数量不匹配"})
        if condition.count("{") != condition.count("}"):
            errors.append({"field": "condition", "message": "条件模板 '{' 与 '}' 数量不匹配"})

        # 模板变量名合法性 + 未定义提示
        template_vars = set(_TEMPLATE_VAR_RE.findall(condition))
        for var in sorted(template_vars):
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", var):
                warnings.append({
                    "field": "condition",
                    "message": f"模板变量名 '{{{var}}}' 不合法（应为字母/下划线开头）",
                })
            elif known_params and var not in known_params:
                hints.append({
                    "field": "condition",
                    "message": f"模板变量 '{{{var}}}' 未在 frontmatter.params 或 policy_pack 中定义",
                })

        # 5) 比较运算符提示
        if not _COMPARISON_HINT_RE.search(condition) and not template_vars:
            tokens = _VAR_TOKEN_RE.findall(condition)
            if tokens:
                hints.append({
                    "field": "condition",
                    "message": (
                        "条件中未发现比较运算符（如 > / < / = 或 大于 / 小于）。"
                        "建议明确变量与阈值的关系，例如 'ROI > 1.5'"
                    ),
                })

    valid = len(errors) == 0
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "hints": hints,
    }


async def validate_skill(db: AsyncSession, skill_id: str) -> dict:
    """
    校验Skill所有块的完整性，返回每块状态。
    用于提交审核前的预检查。
    """
    validate_skill_id(skill_id)

    # 检查Skill是否存在
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    # 读取文件内容
    skill_md = git_service.read_file(skill_id, "SKILL.md") or ""
    policy_pack_raw = git_service.read_file(skill_id, "policy_pack.yaml")
    skillforge_yaml_exists = git_service.read_file(skill_id, "skillforge.yaml") is not None
    contract = _read_contract_for_validation(skill_id)
    script_exists = git_service.read_file(skill_id, "scripts/main.py") is not None

    # 解析SKILL.md
    parsed = skill_parser.parse(skill_md) if skill_md else None

    blocks = []

    # 1. frontmatter（基本信息）
    # 支持两种格式：
    #   旧格式: name/department/trigger_type 在 frontmatter 顶层
    #   标准格式: name/description 在顶层，department 在 metadata 或 skillforge.yaml
    if parsed and parsed.frontmatter:
        fm = parsed.frontmatter
        metadata = fm.get("metadata", {}) or {}
        missing = []

        # name 必填（两种格式都需要）
        if not fm.get("name"):
            missing.append("name")

        # description 必填（标准格式要求）
        if not fm.get("description"):
            missing.append("description")

        # department: 顶层 或 metadata.department 或 skillforge.yaml
        has_department = (
            fm.get("department")
            or metadata.get("department")
            or git_service.read_file(skill_id, "skillforge.yaml") is not None
        )
        if not has_department:
            missing.append("department (frontmatter 或 skillforge.yaml)")

        # trigger_type: 顶层 或 skillforge.yaml
        has_trigger = (
            fm.get("trigger_type")
            or git_service.read_file(skill_id, "skillforge.yaml") is not None
        )
        if not has_trigger:
            missing.append("trigger_type (frontmatter 或 skillforge.yaml)")

        if missing:
            blocks.append({
                "name": "frontmatter",
                "status": "fail",
                "message": f"缺少必填字段: {', '.join(missing)}",
            })
        else:
            # 标准格式额外检查
            warnings = []
            name_val = fm.get("name", "")
            if name_val != name_val.lower() or " " in name_val:
                warnings.append(f"name '{name_val}' 建议用小写+连字符格式")
            msg = "; ".join(warnings) if warnings else ""
            blocks.append({"name": "frontmatter", "status": "pass", "message": msg})
    else:
        blocks.append({
            "name": "frontmatter",
            "status": "fail",
            "message": "缺少YAML frontmatter",
        })

    # 2. purpose（目的）：至少10个字符
    purpose = parsed.purpose.strip() if parsed else ""
    if len(purpose) >= 10:
        blocks.append({"name": "purpose", "status": "pass", "message": ""})
    elif purpose:
        blocks.append({
            "name": "purpose",
            "status": "fail",
            "message": f"目的描述过短: 当前{len(purpose)}字符，至少需要10字符",
        })
    else:
        blocks.append({
            "name": "purpose",
            "status": "fail",
            "message": "缺少目的描述",
        })

    # 3. steps（判断逻辑）：兼容结构化步骤、Markdown 决策阶梯和 contract 工作流
    steps = parsed.steps if parsed else []
    if not steps:
        fallback_ok, fallback_message = _has_markdown_decision_logic(skill_md, contract)
        if fallback_ok:
            blocks.append({
                "name": "steps",
                "status": "pass",
                "message": fallback_message,
            })
        else:
            blocks.append({
                "name": "steps",
                "status": "fail",
                "message": "缺少判断逻辑，至少需要1个步骤",
            })
    else:
        total_branches = sum(len(s.branches) for s in steps)
        empty_branches = []
        for s in steps:
            if not s.branches:
                empty_branches.append(s.id)
            else:
                for b in s.branches:
                    if not b.condition.strip():
                        empty_branches.append(s.id)
                        break
        if empty_branches:
            blocks.append({
                "name": "steps",
                "status": "fail",
                "message": f"步骤 {', '.join(empty_branches)} 缺少有效分支条件",
            })
        else:
            blocks.append({
                "name": "steps",
                "status": "pass",
                "message": f"{len(steps)}个步骤，{total_branches}个分支",
            })

    # 4. antipatterns（反例）：建议至少1个，0个仅warning
    antipatterns = parsed.antipatterns if parsed else []
    if antipatterns:
        blocks.append({
            "name": "antipatterns",
            "status": "pass",
            "message": f"{len(antipatterns)}个反例",
        })
    else:
        blocks.append({
            "name": "antipatterns",
            "status": "warning",
            "message": "建议至少添加1个反例",
        })

    # 5. output_definition（输出定义）：建议至少1项，0项仅warning
    outputs = parsed.output_definition if parsed else []
    if outputs:
        blocks.append({
            "name": "output_definition",
            "status": "pass",
            "message": f"{len(outputs)}项输出定义",
        })
    else:
        blocks.append({
            "name": "output_definition",
            "status": "warning",
            "message": "建议至少添加1项输出定义",
        })

    # 6. data_inputs（数据输入）：仅展示信息，不校验
    data_inputs = parsed.data_inputs if parsed else []
    blocks.append({
        "name": "data_inputs",
        "status": "skip",
        "message": f"{len(data_inputs)}项数据输入" if data_inputs else "无数据输入（不影响提交）",
    })

    # 7. policy_pack（参数）：兼容旧 policy_pack.yaml 和新 contract/frontmatter 参数定义
    if policy_pack_raw is None:
        params_ok, params_message = _has_param_definition(parsed, contract, skillforge_yaml_exists)
        if params_ok:
            blocks.append({
                "name": "policy_pack",
                "status": "pass",
                "message": params_message,
            })
        else:
            blocks.append({
                "name": "policy_pack",
                "status": "fail",
                "message": "缺少 policy_pack.yaml 文件，且未检测到 contract/frontmatter 参数定义",
            })
    else:
        try:
            yaml.safe_load(policy_pack_raw)
            blocks.append({"name": "policy_pack", "status": "pass", "message": ""})
        except yaml.YAMLError as e:
            blocks.append({
                "name": "policy_pack",
                "status": "fail",
                "message": f"policy_pack.yaml 格式错误: {str(e)[:80]}",
            })

    # 8. test_cases（测试用例）：兼容 SKILL.md 用例、tests/ 自动化测试和 fixtures/ 样例
    test_cases = parsed.test_cases if parsed else []
    if len(test_cases) < 3:
        assets_ok, assets_message = _has_external_test_assets(skill_id)
        if assets_ok:
            blocks.append({
                "name": "test_cases",
                "status": "pass",
                "message": assets_message,
            })
        else:
            blocks.append({
                "name": "test_cases",
                "status": "fail",
                "message": f"测试用例不足: 当前{len(test_cases)}个，至少需要3个",
            })
    else:
        incomplete = []
        for tc in test_cases:
            if not tc.input_data or not tc.expected_output:
                incomplete.append(tc.name or "未命名")
        if incomplete:
            blocks.append({
                "name": "test_cases",
                "status": "fail",
                "message": f"以下用例缺少input_data或expected_output: {', '.join(incomplete)}",
            })
        else:
            blocks.append({
                "name": "test_cases",
                "status": "pass",
                "message": f"{len(test_cases)}个测试用例",
            })

    # 9. script（脚本）：检查scripts/main.py存在
    if script_exists:
        blocks.append({"name": "script", "status": "pass", "message": ""})
    else:
        blocks.append({
            "name": "script",
            "status": "fail",
            "message": "缺少 scripts/main.py 脚本文件",
        })

    # 整体是否通过：所有块都不是fail即为通过
    all_valid = all(b["status"] != "fail" for b in blocks)

    return {
        "skill_id": skill_id,
        "valid": all_valid,
        "blocks": blocks,
    }


async def check_data_drift(db: AsyncSession, skill_id: str) -> dict:
    """
    参数漂移检测：对比 SKILL.md data_inputs 与实际 DataSource。
    检测类型：missing_source / column_mismatch / frequency_mismatch / inactive_source
    """
    from app.datasources.models import DataSource, DataIngestionLog

    validate_skill_id(skill_id)

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_NOT_FOUND", 404)

    parsed = skill_parser.parse(skill_md)
    drifts = []

    for data_input in parsed.data_inputs:
        source_name = data_input.source
        if not source_name:
            continue

        # 查找数据源：先按 id 精确匹配，再按 name 模糊匹配
        result = await db.execute(
            select(DataSource).where(DataSource.id == source_name)
        )
        source = result.scalar_one_or_none()
        if not source:
            result = await db.execute(
                select(DataSource).where(DataSource.name == source_name)
            )
            source = result.scalar_one_or_none()

        if not source:
            drifts.append({
                "type": "missing_source",
                "severity": "error",
                "data_input": data_input.name,
                "source_ref": source_name,
                "message": f"数据源 '{source_name}' 不存在",
            })
            continue

        if not source.is_active:
            drifts.append({
                "type": "inactive_source",
                "severity": "warning",
                "data_input": data_input.name,
                "source_ref": source_name,
                "message": f"数据源 '{source_name}' 已停用",
            })

        # 频率对比
        if data_input.frequency and source.schedule:
            if not _frequency_compatible(data_input.frequency, source.schedule):
                drifts.append({
                    "type": "frequency_mismatch",
                    "severity": "warning",
                    "data_input": data_input.name,
                    "source_ref": source_name,
                    "expected": data_input.frequency,
                    "actual": source.schedule,
                    "message": f"频率不匹配: SKILL.md 声明 '{data_input.frequency}', 实际 '{source.schedule}'",
                })

        # 字段对比
        source_columns = source.config.get("columns", []) if source.config else []
        if source_columns and data_input.name:
            if data_input.name not in source_columns:
                drifts.append({
                    "type": "column_mismatch",
                    "severity": "warning",
                    "data_input": data_input.name,
                    "source_ref": source_name,
                    "message": f"字段 '{data_input.name}' 在数据源中未找到",
                })

    return {
        "skill_id": skill_id,
        "total_inputs": len(parsed.data_inputs),
        "drift_count": len(drifts),
        "has_errors": any(d["severity"] == "error" for d in drifts),
        "drifts": drifts,
    }


def _frequency_compatible(declared: str, actual_schedule: str) -> bool:
    """判断 SKILL.md 声明的频率与数据源的 cron 表达式是否兼容"""
    # 自然语言 → 频率关键词映射
    freq_map = {
        "每日": "daily",
        "daily": "daily",
        "每天": "daily",
        "每小时": "hourly",
        "hourly": "hourly",
        "每周": "weekly",
        "weekly": "weekly",
        "实时": "realtime",
    }
    declared_key = freq_map.get(declared.strip(), declared.strip().lower())

    # 从 cron 表达式推断频率
    parts = actual_schedule.strip().split()
    if len(parts) == 5:
        minute, hour, dom, month, dow = parts
        if minute != "*" and hour != "*" and dom == "*" and dow == "*":
            actual_key = "daily"
        elif minute != "*" and hour == "*":
            actual_key = "hourly"
        elif dow != "*" and dom == "*":
            actual_key = "weekly"
        else:
            actual_key = actual_schedule
    else:
        actual_key = actual_schedule.lower()

    return declared_key == actual_key


async def compare_params(
    db: AsyncSession,
    skill_id: str,
    new_params: dict,
) -> dict:
    """
    参数影响预览：对比新旧参数，检查最近决策记录中哪些可能受影响。

    由于无法在本地重新执行Skill逻辑（执行由OpenClaw负责），这里做近似分析：
    1. 找出哪些参数发生了变更（名称+值对比）
    2. 查询最近20条非sandbox的decision_log
    3. 对每条记录，检查其input_snapshot或output_result中是否引用了被修改的参数名
    4. 引用了的标记为"可能受影响"
    """
    validate_skill_id(skill_id)

    # 获取当前policy_pack参数
    policy_pack_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""
    try:
        old_params = yaml.safe_load(policy_pack_raw) or {}
    except yaml.YAMLError:
        old_params = {}

    # 计算哪些参数发生了变化
    changed_params = []
    all_keys = set(list(old_params.keys()) + list(new_params.keys()))
    for key in sorted(all_keys):
        old_val = old_params.get(key)
        new_val = new_params.get(key)
        if old_val != new_val:
            changed_params.append({
                "name": key,
                "old_value": old_val,
                "new_value": new_val,
            })

    # 没有参数变化，直接返回
    if not changed_params:
        return {
            "changed_params": [],
            "affected_decisions": 0,
            "total_decisions": 0,
            "sample_affected": [],
        }

    changed_param_names = [p["name"] for p in changed_params]

    # 查询最近20条非sandbox的决策记录
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == False)  # noqa: E712
        .order_by(DecisionLog.created_at.desc())
        .limit(20)
    )
    result = await db.execute(stmt)
    recent_logs = result.scalars().all()

    total_decisions = len(recent_logs)
    affected_decisions = 0
    sample_affected = []

    for log_entry in recent_logs:
        # 将input_snapshot和output_result序列化为字符串，检查是否包含被修改的参数名
        snapshot_str = json.dumps(log_entry.input_snapshot or {}, ensure_ascii=False)
        result_str = json.dumps(log_entry.output_result or {}, ensure_ascii=False)
        combined_text = snapshot_str + result_str

        # 检查是否引用了任何被修改的参数
        matched_params = [name for name in changed_param_names if name in combined_text]
        if matched_params:
            affected_decisions += 1
            # 最多收集5条样例
            if len(sample_affected) < 5:
                sample_affected.append({
                    "decision_id": log_entry.id,
                    "created_at": isoformat_bjt(log_entry.created_at),
                    "matched_params": matched_params,
                    "suggested_action": log_entry.suggested_action,
                })

    return {
        "changed_params": changed_params,
        "affected_decisions": affected_decisions,
        "total_decisions": total_decisions,
        "sample_affected": sample_affected,
    }
