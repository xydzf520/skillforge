"""AcceptancePolicy — 字典序门槛判定

修复（Codex 审查 M3）: metrics 缺字段时直接判失败，不静默降级。
"""

# 必须包含的指标字段
REQUIRED_METRICS = {"accuracy", "regression_count"}


class AcceptancePolicy:

    def pass_offline(
        self, candidate_score: dict, baseline_score: dict, config: dict,
    ) -> tuple[bool, str]:
        """离线评测门槛判定"""
        acceptance = config.get("acceptance", {})

        # M3: 先校验 metrics schema 完整性
        for field in REQUIRED_METRICS:
            if field not in candidate_score:
                return False, f"候选评测缺少必需指标: {field}"
            if field not in baseline_score:
                return False, f"基线评测缺少必需指标: {field}"

        # 硬门槛: 验证错误
        if candidate_score.get("validation_errors"):
            return False, "结构验证失败"

        # 硬门槛: 零回归
        max_regression = acceptance.get("max_regression_count", 0)
        new_regressions = candidate_score["regression_count"] - baseline_score["regression_count"]
        if new_regressions > max_regression:
            return False, f"新增 {new_regressions} 个回归用例（允许 {max_regression}）"

        # 主目标: 准确率提升
        epsilon = acceptance.get("min_accuracy_gain", 0.02)
        accuracy_delta = candidate_score["accuracy"] - baseline_score["accuracy"]
        if accuracy_delta < epsilon:
            return False, f"准确率提升 {accuracy_delta:.4f} < 阈值 {epsilon}"

        # 次目标: 成本
        cand_cost = candidate_score.get("avg_cost", 0)
        base_cost = baseline_score.get("avg_cost", 0)
        if base_cost > 0 and cand_cost > base_cost * 1.5:
            return False, f"成本恶化 {cand_cost/base_cost:.0%}（上限 150%）"

        return True, f"通过: 准确率 +{accuracy_delta:.4f}"

    def pass_shadow(
        self, shadow_stats: dict, config: dict,
    ) -> tuple[bool, str]:
        acceptance = config.get("acceptance", {})
        min_consistency = acceptance.get("min_consistency_rate", 70)
        min_days = acceptance.get("min_shadow_days", 3)

        days = shadow_stats.get("shadow_days", 0)
        rate = shadow_stats.get("consistency_rate", 0)

        if days < min_days:
            return False, f"Shadow 运行 {days} 天（需 {min_days} 天）"
        if rate < min_consistency:
            return False, f"一致率 {rate:.1f}%（需 {min_consistency}%）"

        return True, f"通过: {days} 天, 一致率 {rate:.1f}%"


acceptance_policy = AcceptancePolicy()
