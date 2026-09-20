"""BenchmarkRunner — 对候选版本跑完整评测"""

import uuid
from datetime import datetime

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.optimizer.models import BenchmarkPack, BenchmarkCase, BenchmarkRun
from app.common.time_utils import isoformat_bjt, now_bjt


class BenchmarkRunner:

    async def run(
        self, pack_id: str, candidate_id: str | None,
        db: AsyncSession,
    ) -> dict:
        """
        跑完整 benchmark pack。
        candidate_id=None 时跑 baseline。
        """
        # 加载 pack
        pack = await db.execute(
            select(BenchmarkPack).where(BenchmarkPack.id == pack_id)
        )
        pack = pack.scalar_one_or_none()
        if not pack:
            raise AppError("NOT_FOUND", 404, {"detail": "评测包不存在"})

        # 修复: 缓存 key 加入 skill_id + candidate_id，且仅冻结 pack 可缓存
        cache_key = f"benchmark:{pack_id}:{pack.skill_id}:{candidate_id or 'baseline'}"
        if pack.frozen:
            try:
                from app.common.cache import cache_get
                cached = await cache_get(cache_key)
                if cached:
                    logger.info(f"命中评测缓存: {cache_key}")
                    return cached
            except Exception as e:
                logger.warning(f"评测缓存读取失败: {e}")

        cases_result = await db.execute(
            select(BenchmarkCase).where(BenchmarkCase.pack_id == pack_id)
        )
        cases = cases_result.scalars().all()
        if not cases:
            raise AppError("EMPTY_PACK", 400, {"detail": "评测包无用例"})

        run_id = f"br-{uuid.uuid4().hex[:12]}"
        run = BenchmarkRun(
            id=run_id, pack_id=pack_id,
            candidate_id=candidate_id, status="running",
        )
        db.add(run)
        await db.flush()

        # 逐 case 执行
        case_results = []
        passed_count = 0
        total_weight = 0.0
        weighted_pass = 0.0

        for case in cases:
            try:
                result = await self._run_single_case(
                    skill_id=pack.skill_id,
                    case=case,
                    candidate_id=candidate_id,
                )
                case_results.append(result)
                total_weight += case.weight
                if result["passed"]:
                    passed_count += 1
                    weighted_pass += case.weight
            except Exception as e:
                logger.warning(f"评测用例 {case.case_key} 执行失败: {e}")
                case_results.append({
                    "case_key": case.case_key,
                    "passed": False,
                    "error": str(e),
                })
                total_weight += case.weight

        # 聚合指标
        accuracy = weighted_pass / total_weight if total_weight > 0 else 0
        regression_count = sum(1 for r in case_results if not r.get("passed"))
        metrics = {
            "accuracy": round(accuracy, 4),
            "passed": passed_count,
            "failed": len(cases) - passed_count,
            "total": len(cases),
            "regression_count": regression_count,
            "weighted_accuracy": round(accuracy, 4),
        }

        await db.execute(
            update(BenchmarkRun)
            .where(BenchmarkRun.id == run_id)
            .values(
                status="completed",
                metrics=metrics,
                case_results=case_results,
                finished_at=now_bjt(),
            )
        )

        logger.info(f"评测完成: {run_id}, accuracy={accuracy:.2%}, pass={passed_count}/{len(cases)}")
        result_data = {
            "run_id": run_id, "pack_id": pack_id,
            "candidate_id": candidate_id,
            "status": "completed", "metrics": metrics,
        }

        # 仅冻结 pack 写入缓存
        if pack.frozen:
            try:
                from app.common.cache import cache_set
                await cache_set(cache_key, result_data, ttl=3600)
            except Exception as e:
                logger.warning(f"评测缓存写入失败: {e}")

        return result_data

    async def _run_single_case(
        self, skill_id: str, case: BenchmarkCase,
        candidate_id: str | None,
    ) -> dict:
        """执行单个评测用例，复用沙箱执行；OpenClaw 不可用时 fallback 到本地模拟"""
        actual_output = {}
        try:
            from app.execution.execution_service import execution_service
            output = await execution_service.execute_skill(
                skill_id=skill_id,
                params=case.input_data,
                sandbox=True,
                triggered_by="optimizer",
            )
            actual_output = output.get("output", {})
        except Exception as e:
            # OpenClaw 不可用 → 本地模拟：用 SKILL.md 解析 + 输入参数做简单规则匹配
            logger.debug(f"沙箱执行失败，使用本地模拟: {e}")
            actual_output = self._local_simulate(skill_id, case.input_data)
        passed = True
        failures = []

        # 检查 assertions
        for assertion in (case.assertions or []):
            field = assertion.get("field", "")
            op = assertion.get("op", "eq")
            expected_val = assertion.get("value")

            # 简单字段取值（支持 output.conclusion 格式）
            actual_val = actual_output
            for part in field.replace("output.", "").split("."):
                if isinstance(actual_val, dict):
                    actual_val = actual_val.get(part)
                else:
                    actual_val = None
                    break

            if op == "eq" and actual_val != expected_val:
                passed = False
                failures.append(f"{field}: 期望 {expected_val}, 实际 {actual_val}")
            elif op == "neq" and actual_val == expected_val:
                passed = False
                failures.append(f"{field}: 不应等于 {expected_val}")

        # 如果有 expected_output 且无 assertions，做整体对比
        if case.expected_output and not case.assertions:
            if actual_output != case.expected_output:
                passed = False
                failures.append("输出与期望不匹配")

        return {
            "case_key": case.case_key,
            "passed": passed,
            "actual_output": actual_output,
            "expected_output": case.expected_output,
            "failures": failures,
        }

    def _local_simulate(self, skill_id: str, input_data: dict) -> dict:
        """本地模拟：解析 SKILL.md，对第一个匹配的分支返回结论"""
        try:
            from app.skills.core.git_service import git_service
            from app.skills.core.parser import skill_parser
            md = git_service.read_file(skill_id, "SKILL.md")
            if not md:
                return {"conclusion": "unknown", "_simulated": True}
            parsed = skill_parser.parse(md)
            if parsed.steps and parsed.steps[0].branches:
                # 返回第一个分支的结论作为模拟输出
                first = parsed.steps[0].branches[0]
                return {
                    "conclusion": first.conclusion,
                    "action": first.action,
                    "_simulated": True,
                }
        except Exception as e:
            logger.debug("benchmark 模拟执行解析失败: {}", e)
        return {"conclusion": "unknown", "_simulated": True}

    async def get_failures(self, run_id: str, db: AsyncSession) -> list:
        """提取失败用例"""
        result = await db.execute(
            select(BenchmarkRun).where(BenchmarkRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if not run or not run.case_results:
            return []
        return [r for r in run.case_results if not r.get("passed")]

    async def get_run(self, run_id: str, db: AsyncSession) -> dict:
        result = await db.execute(
            select(BenchmarkRun).where(BenchmarkRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if not run:
            raise AppError("NOT_FOUND", 404)
        return {
            "run_id": run.id, "pack_id": run.pack_id,
            "candidate_id": run.candidate_id,
            "status": run.status, "metrics": run.metrics,
            "case_results": run.case_results,
            "started_at": isoformat_bjt(run.started_at),
            "finished_at": isoformat_bjt(run.finished_at),
        }


benchmark_runner = BenchmarkRunner()
