"""FailureAnalyzer — 失败用例聚类 + LLM 根因分析

F3: prompt 模板从 PromptRegistry 加载（optimizer_failure_analysis@v1）。
"""

import json

from loguru import logger

from app.common.ai import call_llm_cached
from app.common.prompt_registry import prompt_registry


class FailureAnalyzer:

    async def analyze(
        self, skill_md: str, failures: list[dict], session_id: str,
    ) -> dict:
        """
        对失败用例做聚类 + 根因分析。
        先尝试规则聚类，再用 LLM 深度分析。
        """
        if not failures:
            return {"clusters": [], "summary": "无失败用例"}

        # 规则聚类（快速、免费）
        rule_clusters = self._rule_based_cluster(failures)

        # 如果失败数 >= 3，用 LLM 做深度分析
        if len(failures) >= 3:
            try:
                llm_result = await self._llm_analyze(skill_md, failures, session_id)
                if llm_result and llm_result.get("clusters"):
                    return llm_result
            except Exception as e:
                logger.warning(f"LLM 失败分析异常: {e}")

        return rule_clusters

    def _rule_based_cluster(self, failures: list[dict]) -> dict:
        """基于失败消息的关键词做简单聚类"""
        clusters = {}

        for f in failures:
            # 取第一个失败原因作为分类依据
            reasons = f.get("failures", [])
            key = reasons[0] if reasons else f.get("error", "unknown")

            # 按字段名聚类
            field = ""
            if "期望" in key and "实际" in key:
                # "conclusion: 期望 绿灯, 实际 红灯" → 按 field 聚类
                field = key.split(":")[0].strip() if ":" in key else "output"
            elif "不应等于" in key:
                field = key.split(":")[0].strip() if ":" in key else "output"
            else:
                field = "error"

            cluster_key = f"{field}_mismatch"
            if cluster_key not in clusters:
                clusters[cluster_key] = {
                    "name": f"{field} 不匹配",
                    "count": 0,
                    "case_keys": [],
                    "root_cause": f"字段 {field} 的输出与期望不一致",
                    "suggestion": f"检查影响 {field} 的分支条件和阈值",
                }
            clusters[cluster_key]["count"] += 1
            clusters[cluster_key]["case_keys"].append(f.get("case_key", "?"))

        return {
            "clusters": list(clusters.values()),
            "summary": f"共 {len(failures)} 个失败, {len(clusters)} 类错误模式",
        }

    async def _llm_analyze(
        self, skill_md: str, failures: list[dict], session_id: str,
    ) -> dict | None:
        """LLM 深度聚类分析

        F3: 通过 PromptRegistry 渲染 system prompt，hash 作为 cost_context.prompt_hash。
        """
        failures_text = []
        for f in failures[:15]:
            lines = [f"case_key: {f.get('case_key', '?')}"]
            for msg in f.get("failures", []):
                lines.append(f"  失败: {msg}")
            if f.get("actual_output"):
                lines.append(f"  实际: {json.dumps(f['actual_output'], ensure_ascii=False)[:200]}")
            if f.get("expected_output"):
                lines.append(f"  期望: {json.dumps(f['expected_output'], ensure_ascii=False)[:200]}")
            failures_text.append("\n".join(lines))

        system_prompt, prompt_hash = prompt_registry.build(
            "optimizer_failure_analysis",
            context={
                "skill_md": skill_md[:4000],
                "failures": "\n\n".join(failures_text),
            },
        )

        cache_key = f"optimizer:failure:{session_id}:{len(failures)}"
        result = await call_llm_cached(
            cache_key, system_prompt,
            "请分析这些失败用例，按根因聚类。",
            cache_ttl=600, max_tokens=1500, temperature=0.2,
            call_source="optimizer_failure_analysis",
            cost_context={
                "conversation_id": session_id,
                "prompt_hash": prompt_hash,
            },
        )

        return result


failure_analyzer = FailureAnalyzer()
