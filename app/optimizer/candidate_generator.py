"""CandidateGenerator — LLM 生成结构化候选 + 应用到 AST

修复清单（Codex 审查）:
- H2: LLM 输出 schema 校验，单条 change try-catch
- H3: editable_zones 严格枚举（非子串匹配）
- H4: 缓存 key 加入 prompt hash
- M4: no-op candidate 检测（AST 无变化 → 返回 None）
- M5: branch_order 必须是排列

F3: prompt 模板从 PromptRegistry 加载（optimizer_generate_candidate@v1），
    用统一的 prompt_hash 写入 cost_context，跨链路可比对。
"""

import copy
import json
import uuid
from datetime import datetime

from loguru import logger

from app.common.ai import call_llm
from app.common.prompt_registry import prompt_registry
from app.optimizer.models import OptimizerCandidate
from app.skills.core.parser import skill_parser, SkillStructured, Antipattern
from app.common.time_utils import now_bjt

# H3: 合法 zone 枚举
VALID_ZONES = {"branch_conditions", "thresholds", "branch_order", "antipatterns"}
# H3: 合法 field 枚举（按 zone）
VALID_FIELDS = {
    "branch_conditions": {"condition", "conclusion", "action", "next_step"},
    "thresholds": {"condition", "conclusion", "action", "next_step"},
    "branch_order": {"order"},
    "antipatterns": {"add"},
}


class CandidateGenerator:

    async def generate(
        self,
        session_id: str,
        iteration: int,
        skill_md: str,
        parsed: SkillStructured,
        failures: list[dict],
        decision_logs: list[dict],
        config: dict,
        *,
        skill_id: str | None = None,
    ) -> OptimizerCandidate | None:
        """生成一个候选版本

        F4: 可选 skill_id 用于 LLM 成本归因（写 usage_logs.skill_id）。
        """
        goal = config.get("goal", "提高准确率")
        editable_zones = set(config.get("editable_zones", ["branch_conditions", "thresholds"]))
        frozen_zones = config.get("frozen_zones", ["frontmatter", "output_definition"])

        # F3: 通过 PromptRegistry 渲染 system prompt（hash 用于跨链路成本归因）
        system_prompt, prompt_hash = prompt_registry.build(
            "optimizer_generate_candidate",
            context={
                "skill_md": skill_md[:6000],
                "goal": goal,
                "editable_zones": ", ".join(editable_zones),
                "frozen_zones": ", ".join(frozen_zones),
                "failures": self._format_failures(failures[:10]),
                "decision_logs": self._format_logs(decision_logs[:10]),
            },
        )

        # 直接调 LLM（不缓存 — 每轮迭代上下文不同，缓存命中率低且有旧值风险）
        # F4: 写入 usage_logs 用 session_id 作 conversation_id；skill_id/prompt_hash 透传
        result = await call_llm(
            system_prompt,
            f"请基于失败用例分析，生成一个优化修改方案。目标: {goal}",
            max_tokens=2000, temperature=0.4, timeout=120,
            call_source="optimizer",
            cost_context={
                "skill_id": skill_id,
                "conversation_id": session_id,
                "prompt_hash": prompt_hash,
            },
        )

        if not result:
            logger.warning(f"候选生成 LLM 返回空: session={session_id}, iter={iteration}")
            return None

        # H2: schema 校验
        changes = result.get("changes", [])
        rationale = result.get("rationale", "")
        if not isinstance(changes, list) or not changes:
            logger.warning(f"LLM 未返回有效 changes: {type(changes)}")
            return None

        validated_changes = self._validate_changes(changes, editable_zones)
        if not validated_changes:
            logger.warning(f"所有 changes 未通过校验")
            return None

        # 应用变更到 AST
        try:
            new_parsed = self._apply_changes(parsed, validated_changes, editable_zones)
            new_md = skill_parser.render(new_parsed)
        except Exception as e:
            logger.error(f"应用变更失败: {e}")
            return None

        # M4: no-op 检测 — 如果渲染结果和原始一样，返回 None
        original_md = skill_parser.render(parsed)
        if new_md.strip() == original_md.strip():
            logger.warning(f"候选与原始相同 (no-op)，跳过")
            return None

        diff_summary = self._generate_diff_summary(validated_changes)

        candidate = OptimizerCandidate(
            id=f"cand-{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            iteration=iteration,
            skill_md_patch=new_md,
            diff_summary=diff_summary,
            generation_rationale=rationale,
            changes=validated_changes,
            model_name=str(result.get("_model", "")),
            token_count=result.get("_tokens", 0) if isinstance(result, dict) else 0,
            created_at=now_bjt(),
        )

        logger.info(f"生成候选: {candidate.id}, changes={len(validated_changes)}, summary={diff_summary}")
        return candidate

    def _validate_changes(self, changes: list, editable_zones: set) -> list[dict]:
        """H2: 逐条校验 LLM 输出的 change，过滤非法项"""
        valid = []
        for i, change in enumerate(changes):
            try:
                if not isinstance(change, dict):
                    logger.warning(f"change[{i}] 非 dict，跳过")
                    continue

                zone = str(change.get("zone", ""))

                # H3: 严格枚举校验（非子串匹配）
                if zone not in VALID_ZONES:
                    logger.warning(f"change[{i}] zone '{zone}' 不在合法枚举中，跳过")
                    continue
                if zone not in editable_zones:
                    logger.warning(f"change[{i}] zone '{zone}' 不在可编辑区域中，跳过")
                    continue

                field = str(change.get("field", ""))
                if field and zone in VALID_FIELDS and field not in VALID_FIELDS[zone]:
                    logger.warning(f"change[{i}] field '{field}' 不合法，跳过")
                    continue

                # branch_idx 类型校验
                branch_idx = change.get("branch")
                if branch_idx is not None:
                    if not isinstance(branch_idx, int) or branch_idx < 0:
                        logger.warning(f"change[{i}] branch index 无效: {branch_idx}")
                        continue

                valid.append(change)
            except Exception as e:
                logger.warning(f"change[{i}] 校验异常: {e}")
                continue

        return valid

    def _apply_changes(
        self, parsed: SkillStructured, changes: list[dict], editable_zones: set,
    ) -> SkillStructured:
        """应用结构化变更到 AST（深拷贝，不修改原对象）"""
        new = copy.deepcopy(parsed)

        for change in changes:
            zone = change.get("zone", "")
            step_id = change.get("step", "")
            branch_idx = change.get("branch")
            field = change.get("field", "")
            new_value = change.get("new", "")

            # H2: 每条 change 独立 try-catch，一条失败不影响其他
            try:
                if zone in ("branch_conditions", "thresholds") and step_id:
                    step = self._find_step(new, step_id)
                    if not step:
                        logger.warning(f"step '{step_id}' 不存在，跳过")
                        continue
                    if branch_idx is None or branch_idx >= len(step.branches):
                        logger.warning(f"branch index {branch_idx} 越界 (max {len(step.branches)-1})，跳过")
                        continue
                    branch = step.branches[branch_idx]
                    if field == "condition":
                        branch.condition = str(new_value)
                    elif field == "conclusion":
                        branch.conclusion = str(new_value)
                    elif field == "action":
                        branch.action = str(new_value)
                    elif field == "next_step":
                        branch.next_step = str(new_value) if new_value else None

                elif zone == "branch_order" and step_id:
                    step = self._find_step(new, step_id)
                    if step and isinstance(new_value, list):
                        indices = [int(i) for i in new_value]
                        # M5: 必须是原索引集合的排列
                        expected = set(range(len(step.branches)))
                        if set(indices) != expected:
                            logger.warning(f"branch_order 不是合法排列: {indices} vs {expected}")
                            continue
                        step.branches = [step.branches[i] for i in indices]

                elif zone == "antipatterns" and field == "add" and new_value:
                    new.antipatterns.append(Antipattern(
                        scenario=new_value.get("scenario", "") if isinstance(new_value, dict) else str(new_value),
                        correct_action=new_value.get("correct_action", "") if isinstance(new_value, dict) else "",
                    ))

            except Exception as e:
                logger.warning(f"应用 change 失败 (zone={zone}, step={step_id}): {e}")
                continue

        return new

    def _find_step(self, parsed: SkillStructured, step_id: str):
        for step in parsed.steps:
            if step.id == step_id or step.id == step_id.replace("step_", ""):
                return step
        return None

    def _format_failures(self, failures: list[dict]) -> str:
        lines = []
        for f in failures:
            lines.append(f"- case: {f.get('case_key', '?')}")
            if f.get("failures"):
                for msg in f["failures"]:
                    lines.append(f"  失败: {msg}")
            if f.get("actual_output"):
                lines.append(f"  实际输出: {json.dumps(f['actual_output'], ensure_ascii=False)[:200]}")
            if f.get("expected_output"):
                lines.append(f"  期望输出: {json.dumps(f['expected_output'], ensure_ascii=False)[:200]}")
        return "\n".join(lines) or "（无失败用例）"

    def _format_logs(self, logs: list[dict]) -> str:
        lines = []
        for log in logs:
            action = log.get("user_action", "?")
            lines.append(f"- [{action}] input={json.dumps(log.get('input_snapshot', {}), ensure_ascii=False)[:150]}")
            if log.get("reject_reason"):
                lines.append(f"  拒绝原因: {log['reject_reason']}")
        return "\n".join(lines) or "（无历史日志）"

    def _generate_diff_summary(self, changes: list[dict]) -> str:
        parts = []
        for c in changes[:5]:
            step = c.get("step", "")
            field = c.get("field", "")
            old = str(c.get("old", ""))[:30]
            new = str(c.get("new", ""))[:30]
            parts.append(f"{step}.{field}: {old} → {new}")
        return "; ".join(parts) or "无变更"


candidate_generator = CandidateGenerator()
