"""
测试用例自动生成：从决策树提取所有分支路径，生成骨架测试用例。
工程师只需填入 __TODO__ 处的实际参数值。
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import DecisionLog
from app.skills.core.parser import SkillStructured, DecisionStep, Branch


async def generate_test_cases_ai(
    parsed: SkillStructured, strategy: str = "branch_coverage"
) -> list[dict]:
    """
    B7: 增强测试生成，支持多种策略。
    - branch_coverage: 分支覆盖（原有逻辑，同步调用）
    - mutation: 突变测试（AI生成）
    - boundary: 边界值（原有 + AI增强）
    - adversarial: 对抗测试（基于反例，AI生成）
    - comprehensive: 全部策略组合
    """
    if strategy == "branch_coverage":
        return generate_test_cases(parsed, include_edge_cases=True)

    if strategy == "comprehensive":
        # list(...) 建新 list 避免污染 generate_test_cases 内部的缓存/共享引用
        base = list(generate_test_cases(parsed, include_edge_cases=True))
        for s in ["mutation", "adversarial"]:
            extra = await generate_test_cases_ai(parsed, s)
            base.extend(extra)
        return base

    # AI 生成策略
    import json
    from app.common.ai import call_llm

    steps_data = [
        {"id": s.id, "name": s.name, "branches": [
            {"condition": b.condition, "conclusion": b.conclusion, "action": b.action}
            for b in s.branches
        ]}
        for s in parsed.steps
    ]
    antipatterns_data = [
        {"scenario": a.scenario, "correct_action": a.correct_action}
        for a in parsed.antipatterns
    ]

    strategy_prompts = {
        "mutation": (
            "为以下决策树生成突变测试用例。将参数改为极端值(0/负数/极大值)，验证Skill是否正确处理异常。"
        ),
        "boundary": (
            "为以下决策树生成边界值测试用例。找到所有数值阈值，在阈值-1/阈值/阈值+1各生成一个用例。"
        ),
        "adversarial": (
            "为以下决策树生成对抗测试用例。基于反例列表，为每个反例构造触发场景，验证反例规则是否生效。"
        ),
    }

    system = (
        f"{strategy_prompts.get(strategy, strategy_prompts['mutation'])}"
        "输出纯JSON: {\"test_cases\": [{\"name\": \"用例名\", \"input\": {参数}, "
        "\"expected_output\": {预期}, \"assert_rules\": [\"断言\"]}]}"
    )
    user = (
        f"## 决策树\n```json\n{json.dumps(steps_data, ensure_ascii=False, indent=1)}\n```\n\n"
        f"## 反例列表\n```json\n{json.dumps(antipatterns_data, ensure_ascii=False)}\n```\n\n"
        f"生成3-5个{strategy}测试用例。"
    )

    result = await call_llm(system, user, max_tokens=2000, timeout=30)
    if result and "test_cases" in result:
        for tc in result["test_cases"]:
            tc["auto_generated"] = True
            tc["strategy"] = strategy
        return result["test_cases"]
    return []


def generate_test_cases(parsed: SkillStructured, include_edge_cases: bool = True) -> list[dict]:
    """
    从 SkillStructured 生成测试用例骨架。
    1. 枚举所有从 step_1 到叶子的路径
    2. 从每条路径的 branch.condition 提取参数名和阈值
    3. 生成满足条件的参数值骨架
    """
    if not parsed.steps:
        return []

    # 构建 step_id → step 索引
    step_map = {s.id: s for s in parsed.steps if s.id}

    # 枚举所有路径
    paths: list[list[dict]] = []
    _enumerate_paths(parsed.steps[0], step_map, [], paths)

    # 为每条路径生成测试用例
    cases = []
    for i, path in enumerate(paths):
        case = _path_to_test_case(i + 1, path, parsed)
        cases.append(case)

    # 边界值用例
    if include_edge_cases:
        edge_cases = _generate_edge_cases(parsed)
        cases.extend(edge_cases)

    return cases


def _enumerate_paths(
    step: DecisionStep,
    step_map: dict[str, DecisionStep],
    current_path: list[dict],
    all_paths: list[list[dict]],
    visited: set[str] | None = None,
):
    """递归枚举所有分支路径（带环检测）"""
    if visited is None:
        visited = set()

    # 环检测：如果当前步骤已访问过，终止该路径
    step_key = step.id or id(step)
    if step_key in visited:
        all_paths.append(current_path)
        return
    visited = visited | {step_key}  # 不修改原 set，每条路径独立

    if not step.branches:
        all_paths.append(current_path + [{"step": step, "branch": None}])
        return

    for branch in step.branches:
        new_path = current_path + [{"step": step, "branch": branch}]

        if branch.next_step and branch.next_step in step_map:
            _enumerate_paths(step_map[branch.next_step], step_map, new_path, all_paths, visited)
        else:
            all_paths.append(new_path)


def _path_to_test_case(index: int, path: list[dict], parsed: SkillStructured) -> dict:
    """将一条分支路径转为测试用例骨架"""
    # 提取路径描述
    parts = []
    for p in path:
        step_name = p["step"].name or p["step"].id
        if p["branch"]:
            parts.append(f"{step_name}[{p['branch'].conclusion or '?'}]")
        else:
            parts.append(step_name)
    path_desc = " → ".join(parts)

    # 提取条件中涉及的参数
    params = {}
    for p in path:
        if p["branch"]:
            extracted = _extract_params_from_condition(p["branch"].condition)
            params.update(extracted)

    # 预期输出
    last_branch = path[-1].get("branch")
    expected = {}
    if last_branch:
        if last_branch.conclusion:
            expected["conclusion"] = last_branch.conclusion
        if last_branch.action:
            expected["action"] = last_branch.action

    return {
        "name": f"路径{index}: {path_desc}",
        "input": params,
        "expected_output": expected,
        "assert_rules": [
            f"output.conclusion == '{last_branch.conclusion}'"
        ] if last_branch and last_branch.conclusion else [],
        "path_description": path_desc,
        "auto_generated": True,
    }


def _extract_params_from_condition(condition: str) -> dict:
    """
    从条件字符串提取参数占位符和建议值。
    例：'ROI > 盈亏线 × {roi_green_ratio}' → {'roi_green_ratio': '__TODO__'}
    """
    if not condition:
        return {}
    params = {}
    # 匹配 {param_name} 格式
    for match in re.finditer(r"\{(\w+)\}", condition):
        params[match.group(1)] = "__TODO__"
    return params


def _generate_edge_cases(parsed: SkillStructured) -> list[dict]:
    """生成边界值测试用例：从条件中提取数值阈值"""
    cases = []
    seen_thresholds: set[str] = set()

    # 匹配 "metric op number"，metric 支持中英文 / 下划线
    metric_re = re.compile(
        r"([\u4e00-\u9fffA-Za-z_][\u4e00-\u9fff\w]*)\s*[<>]=?\s*([-]?\d+(?:\.\d+)?)"
    )

    for step in parsed.steps:
        for branch in step.branches:
            if not branch.condition:
                continue
            # 优先匹配 "metric op number"，能拿到字段名
            structured_matches = metric_re.findall(branch.condition)
            if structured_matches:
                for metric, threshold in structured_matches:
                    key = f"{step.id}_{metric}_{threshold}"
                    if key in seen_thresholds:
                        continue
                    seen_thresholds.add(key)
                    try:
                        val = float(threshold)
                    except ValueError:
                        continue
                    cases.append({
                        "name": f"边界值: {step.name or step.id} {metric}={threshold}",
                        "input": {metric: val},
                        "expected_output": {},
                        "assert_rules": [],
                        "path_description": f"边界值测试 {step.name}: {metric}={threshold}",
                        "auto_generated": True,
                        "is_edge_case": True,
                        "_boundary_metric": metric,
                    })
                continue

            # 兜底：未识别 metric 名时仍生成占位用例（保留原行为）
            thresholds = re.findall(r"([\d]+\.?\d*)", branch.condition)
            for threshold in thresholds:
                key = f"{step.id}_{threshold}"
                if key in seen_thresholds:
                    continue
                seen_thresholds.add(key)
                try:
                    val = float(threshold)
                except ValueError:
                    continue
                cases.append({
                    "name": f"边界值: {step.name or step.id} 阈值={threshold}",
                    "input": {"__boundary_value__": val},
                    "expected_output": {},
                    "assert_rules": [],
                    "path_description": f"边界值测试 {step.name}: 阈值 {threshold}",
                    "auto_generated": True,
                    "is_edge_case": True,
                })
    return cases


# ═══════════════════════════════════════════════════════
# 自动回填：从历史决策日志拉取真实样本，替换 __TODO__
# ═══════════════════════════════════════════════════════

# 占位符常量：被识别为"待回填"的输入值
_PLACEHOLDER_VALUES = {"__TODO__", "__BOUNDARY_VALUE__", "__boundary_value__"}


async def auto_fill_test_inputs(
    db: AsyncSession,
    skill_id: str,
    cases: list[dict],
    *,
    sample_limit: int = 100,
) -> dict:
    """从 decision_log 拉历史样本，把 cases 里的 __TODO__ 占位符换成真实值。

    回填策略（按优先级）：
      1. case.input 中已有具体值 → 不动
      2. 字段名匹配历史样本 input_snapshot 中的某个 key → 取最近一条值
      3. 边界值用例（is_edge_case=True，单字段 metric=阈值）→ 已有真实数值，不动
      4. 找不到匹配 → 保留 __TODO__ 并记录到 unfilled_keys

    Args:
        db: 异步 SQLAlchemy session
        skill_id: Skill ID
        cases: generate_test_cases 产出的用例列表（会被原地修改并返回）
        sample_limit: 最多拉取多少条历史日志

    Returns:
        {
            "cases": [...],          # 回填后的用例列表
            "samples_used": int,     # 实际查询到的样本数
            "filled_count": int,     # 成功回填的占位符数
            "unfilled_keys": [...],  # 仍未填的字段名（按字段去重）
        }
    """
    if not cases:
        return {"cases": cases, "samples_used": 0, "filled_count": 0, "unfilled_keys": []}

    import json as _json

    # 1. 拉历史样本（只取生产环境的非沙箱记录，更接近真实分布）
    # [H4] 用 .is_(False) 而不是 == False, 避免 SQLAlchemy E712 歧义
    stmt = (
        select(DecisionLog.input_snapshot)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox.is_(False))
        .where(DecisionLog.input_snapshot.isnot(None))
        .order_by(DecisionLog.created_at.desc())
        .limit(sample_limit)
    )
    try:
        rows = (await db.execute(stmt)).all()
    except Exception as exc:
        # SQL 失败 → 降级到不回填, 不抛 (前端能看到 unfilled_keys 列表)
        from loguru import logger
        logger.warning(f"auto_fill_test_inputs SQL 失败 skill={skill_id}: {exc}")
        return {
            "cases": cases,
            "samples_used": 0,
            "filled_count": 0,
            "unfilled_keys": [],
            "error": "history_query_failed",
        }

    # 2. 构建字段→历史值池（按时间从新到旧, order_by 已保证）
    # [H4] snapshot 类型兜底: PostgreSQL JSONB 通常自动反序列化为 dict, 但若驱动配置
    # 或老数据返回 str, 必须手动 json.loads, 否则丢失整条样本
    field_pool: dict[str, list] = {}
    for (snapshot,) in rows:
        if isinstance(snapshot, str):
            try:
                snapshot = _json.loads(snapshot)
            except (ValueError, TypeError):
                continue
        if not isinstance(snapshot, dict):
            continue
        for k, v in snapshot.items():
            if v is None or isinstance(v, (dict, list)):
                continue
            field_pool.setdefault(k, []).append(v)

    # 3. 逐条用例回填
    total_filled = 0
    unfilled: set[str] = set()
    for case in cases:
        inp = case.get("input")
        if not isinstance(inp, dict):
            continue
        case_filled = 0  # [H4] per-case 计数, 修复全局 filled_count > 0 污染所有 case 的 bug
        for key, val in list(inp.items()):
            # 只处理占位符；具体数值（含边界值用例的真实数字）跳过
            if not isinstance(val, str) or val not in _PLACEHOLDER_VALUES:
                continue
            if key in field_pool and field_pool[key]:
                inp[key] = field_pool[key][0]  # 取最近值
                case_filled += 1
            else:
                unfilled.add(key)
        total_filled += case_filled
        # 标记回填来源 — 仅当本 case 真实回填了至少一个字段
        if case_filled > 0:
            case.setdefault("_autofill_source", "decision_log")

    return {
        "cases": cases,
        "samples_used": len(rows),
        "filled_count": total_filled,
        "unfilled_keys": sorted(unfilled),
    }
