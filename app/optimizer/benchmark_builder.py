"""BenchmarkBuilder — 从历史数据构建冻结评测包"""

import uuid
from datetime import datetime

from loguru import logger
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt
from app.execution.models import DecisionLog
from app.optimizer.models import BenchmarkPack, BenchmarkCase
from app.skills.core.git_service import git_service
from app.skills.core.parser import skill_parser


class BenchmarkBuilder:

    async def build(
        self, skill_id: str, name: str, source: str,
        description: str | None, max_cases: int, min_rating: int | None,
        user_id: str, db: AsyncSession,
    ) -> dict:
        """从 DecisionLog + test_cases 构建评测包"""
        pack_id = f"bp-{uuid.uuid4().hex[:12]}"
        pack = BenchmarkPack(
            id=pack_id, skill_id=skill_id, name=name,
            description=description, source=source,
            created_by=user_id,
        )
        db.add(pack)
        cases = []

        # 从 DecisionLog 提取（有人工反馈的）
        if source in ("decision_log", "mixed"):
            query = (
                select(DecisionLog)
                .where(DecisionLog.skill_id == skill_id)
                .where(DecisionLog.is_sandbox == False)  # noqa: E712
                .where(DecisionLog.user_action.isnot(None))
                .order_by(DecisionLog.created_at.desc())
                .limit(max_cases)
            )
            if min_rating:
                query = query.where(DecisionLog.rating >= min_rating)
            result = await db.execute(query)
            logs = result.scalars().all()

            for i, log in enumerate(logs):
                cases.append(BenchmarkCase(
                    pack_id=pack_id,
                    case_key=f"log-{log.id}",
                    input_data=log.input_snapshot or {},
                    expected_output=log.output_result if log.user_action == "completed" else None,
                    assertions=self._infer_assertions(log),
                    tags=["production", "human_validated"] if log.user_action == "completed" else ["production", "rejected"],
                    weight=float(log.rating or 3) / 5.0,
                    provenance={
                        "decision_log_id": log.id,
                        "user_action": log.user_action,
                        "rating": log.rating,
                        "reject_reason": log.reject_reason,
                    },
                ))

        # 从 SKILL.md test_cases 提取
        if source in ("test_cases", "mixed"):
            skill_md = git_service.read_file(skill_id, "SKILL.md")
            if skill_md:
                try:
                    parsed = skill_parser.parse(skill_md)
                    for tc in (parsed.test_cases or []):
                        cases.append(BenchmarkCase(
                            pack_id=pack_id,
                            case_key=f"tc-{tc.name}",
                            input_data=tc.input_data if hasattr(tc, "input_data") else {},
                            expected_output=tc.expected_output if hasattr(tc, "expected_output") else None,
                            assertions=[],
                            tags=["test_case"],
                            weight=1.0,
                            provenance={"source": "skill_md"},
                        ))
                except Exception as e:
                    logger.warning(f"解析 SKILL.md test_cases 失败: {e}")

        # 去重
        seen_keys = set()
        unique_cases = []
        for c in cases:
            if c.case_key not in seen_keys:
                seen_keys.add(c.case_key)
                unique_cases.append(c)
        cases = unique_cases[:max_cases]

        for c in cases:
            db.add(c)
        pack.case_count = len(cases)
        await db.flush()

        logger.info(f"构建评测包: {pack_id}, skill={skill_id}, cases={len(cases)}")
        return {
            "pack_id": pack_id, "skill_id": skill_id,
            "name": name, "case_count": len(cases),
            "source": source, "frozen": False,
        }

    def _infer_assertions(self, log: DecisionLog) -> list:
        """从 DecisionLog 推导 assertions"""
        assertions = []
        if log.user_action == "completed" and log.output_result:
            # 用户确认的执行结果，输出应该一致
            for key in ("conclusion", "signal", "decision"):
                if key in (log.output_result or {}):
                    assertions.append({
                        "field": f"output.{key}",
                        "op": "eq",
                        "value": log.output_result[key],
                    })
        elif log.user_action == "rejected":
            # 被拒绝的结果，输出应该不同
            if log.output_result and "conclusion" in log.output_result:
                assertions.append({
                    "field": "output.conclusion",
                    "op": "neq",
                    "value": log.output_result["conclusion"],
                    "note": f"被拒绝: {log.reject_reason or ''}",
                })
        return assertions

    async def get_pack(self, pack_id: str, db: AsyncSession) -> dict:
        result = await db.execute(
            select(BenchmarkPack).where(BenchmarkPack.id == pack_id)
        )
        pack = result.scalar_one_or_none()
        if not pack:
            raise AppError("NOT_FOUND", 404)

        # 加载 cases
        cases_result = await db.execute(
            select(BenchmarkCase).where(BenchmarkCase.pack_id == pack_id)
        )
        cases = cases_result.scalars().all()

        return {
            "pack_id": pack.id, "skill_id": pack.skill_id,
            "name": pack.name, "description": pack.description,
            "source": pack.source, "case_count": pack.case_count,
            "frozen": pack.frozen,
            "created_by": pack.created_by,
            "created_at": isoformat_bjt(pack.created_at),
            "cases": [
                {
                    "id": c.id, "case_key": c.case_key,
                    "input_data": c.input_data,
                    "expected_output": c.expected_output,
                    "assertions": c.assertions,
                    "tags": c.tags, "weight": c.weight,
                }
                for c in cases
            ],
        }

    async def freeze(self, pack_id: str, db: AsyncSession) -> dict:
        await db.execute(
            update(BenchmarkPack)
            .where(BenchmarkPack.id == pack_id)
            .values(frozen=True)
        )
        return {"pack_id": pack_id, "frozen": True}


    async def auto_update(self, pack_id: str, db: AsyncSession) -> dict:
        """
        自动补充新 DecisionLog 到已有 pack（不修改已冻结 pack，创建新版本）。
        """
        pack_result = await db.execute(
            select(BenchmarkPack).where(BenchmarkPack.id == pack_id)
        )
        pack = pack_result.scalar_one_or_none()
        if not pack:
            raise AppError("NOT_FOUND", 404)

        if pack.frozen:
            # 冻结的 pack 不能修改，创建新版本
            new_result = await self.build(
                skill_id=pack.skill_id,
                name=f"{pack.name}-v{pack.case_count + 1}",
                source=pack.source,
                description=f"自动更新自 {pack.id}",
                max_cases=300, min_rating=None,
                user_id="system", db=db,
            )
            return {"action": "created_new", **new_result}

        # 非冻结 pack：追加新的 DecisionLog
        existing_keys = set()
        cases_result = await db.execute(
            select(BenchmarkCase.case_key).where(BenchmarkCase.pack_id == pack_id)
        )
        for row in cases_result:
            existing_keys.add(row[0])

        query = (
            select(DecisionLog)
            .where(DecisionLog.skill_id == pack.skill_id)
            .where(DecisionLog.is_sandbox == False)  # noqa: E712
            .where(DecisionLog.user_action.isnot(None))
            .order_by(DecisionLog.created_at.desc())
            .limit(50)
        )
        result = await db.execute(query)
        new_count = 0
        for log in result.scalars().all():
            key = f"log-{log.id}"
            if key not in existing_keys:
                db.add(BenchmarkCase(
                    pack_id=pack_id,
                    case_key=key,
                    input_data=log.input_snapshot or {},
                    expected_output=log.output_result if log.user_action == "completed" else None,
                    assertions=self._infer_assertions(log),
                    tags=["production", "auto_update"],
                    weight=float(log.rating or 3) / 5.0,
                    provenance={"decision_log_id": log.id, "user_action": log.user_action},
                ))
                new_count += 1

        if new_count > 0:
            pack.case_count = (pack.case_count or 0) + new_count
            await db.flush()

        logger.info(f"自动更新 pack {pack_id}: 新增 {new_count} 条")
        return {"pack_id": pack_id, "new_cases": new_count, "total": pack.case_count}


benchmark_builder = BenchmarkBuilder()
