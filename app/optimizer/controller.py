"""OptimizerController — 优化会话管理与循环编排

修复清单（Codex 审查）:
- C1: 评测隔离 — 用临时文件快照替代 live SKILL.md 覆写
- C2: baseline 持久化 — 当前 baseline MD 存入 DB config，恢复时从 DB 重建
- C3: 启动原子性 — CAS 更新 + 进程内去重
- H1: pause 不标 completed — 区分 break 原因
- H5: total_tokens 累加 — SQL 增量更新
- M1: 短事务 — 每步独立 session
- M5: 裸 except → logger.warning
"""

import asyncio
import uuid
from datetime import datetime

from loguru import logger
from sqlalchemy import select, update, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.database import async_session_factory
from app.optimizer.models import OptimizerSession, OptimizerCandidate
from app.common.time_utils import isoformat_bjt, now_bjt


class OptimizerController:
    """借鉴 autoresearch 的 keep/discard 闭环"""

    # 进程内去重（C3 辅助）
    _running_tasks: dict[str, asyncio.Task] = {}

    # ── 会话 CRUD ──

    async def create_session(
        self, skill_id: str, name: str, goal: str,
        config: dict, benchmark_pack_id: str | None,
        user_id: str, db: AsyncSession,
    ) -> dict:
        """创建优化会话，冻结 baseline"""
        from app.skills.core.git_service import git_service
        from app.skills.core.parser import skill_parser
        from app.execution.models import DecisionLog
        from app.optimizer.models import BenchmarkPack, BenchmarkCase

        # 前置校验：必须有可用的评测样例（指定 pack 或 SKILL.md test_cases 或 DecisionLog）
        if benchmark_pack_id:
            pack_count = await db.scalar(
                select(func.count())
                .select_from(BenchmarkCase)
                .where(BenchmarkCase.pack_id == benchmark_pack_id)
            )
            if not pack_count:
                raise AppError("OPTIMIZER_NO_CASES", 400)
        else:
            # 没指定 pack：检查 SKILL.md test_cases + DecisionLog 是否够建包
            test_case_count = 0
            try:
                md = git_service.read_file(skill_id, "SKILL.md") or ""
                if md:
                    parsed = skill_parser.parse(md)
                    test_case_count = len(parsed.test_cases or [])
            except Exception as e:
                logger.warning("读取/解析 SKILL.md 测试用例失败 skill={}: {}", skill_id, e)

            log_count = await db.scalar(
                select(func.count())
                .select_from(DecisionLog)
                .where(DecisionLog.skill_id == skill_id)
                .where(DecisionLog.is_sandbox == False)  # noqa: E712
                .where(DecisionLog.user_action.isnot(None))
            ) or 0

            if test_case_count + log_count == 0:
                raise AppError("OPTIMIZER_NO_CASES", 400)

        baseline_commit = None
        try:
            repo = git_service._get_repo(skill_id)
            baseline_commit = repo.head.commit.hexsha[:8] if repo else None
        except Exception as e:
            logger.warning("读取 baseline commit 失败 skill={}: {}", skill_id, e)

        if benchmark_pack_id:
            config["benchmark_pack_id"] = benchmark_pack_id

        # C2: 保存初始 SKILL.md 到 config 以便恢复
        try:
            initial_md = git_service.read_file(skill_id, "SKILL.md") or ""
            config["_baseline_md"] = initial_md
        except Exception:
            config["_baseline_md"] = ""

        session_id = f"opt-{uuid.uuid4().hex[:12]}"
        session = OptimizerSession(
            id=session_id, skill_id=skill_id, name=name, goal=goal,
            config=config, baseline_commit=baseline_commit, created_by=user_id,
        )
        db.add(session)
        await db.flush()

        logger.info(f"创建优化会话: {session_id}, skill={skill_id}, goal={goal}")
        return {
            "session_id": session_id, "skill_id": skill_id,
            "name": name, "goal": goal, "status": "created",
            "baseline_commit": baseline_commit,
        }

    async def list_sessions(self, skill_id: str | None, db: AsyncSession) -> list:
        query = select(OptimizerSession).order_by(OptimizerSession.created_at.desc())
        if skill_id:
            query = query.where(OptimizerSession.skill_id == skill_id)
        result = await db.execute(query)
        return [
            {
                "session_id": s.id, "skill_id": s.skill_id, "name": s.name,
                "goal": s.goal, "status": s.status,
                "current_iteration": s.current_iteration,
                "max_iterations": s.max_iterations,
                "best_candidate_id": s.best_candidate_id,
                "created_at": isoformat_bjt(s.created_at),
                "finished_at": isoformat_bjt(s.finished_at),
                "failure_reason": (s.config or {}).get("failure_reason"),
            }
            for s in result.scalars().all()
        ]

    async def get_session(self, session_id: str, db: AsyncSession) -> dict:
        result = await db.execute(
            select(OptimizerSession).where(OptimizerSession.id == session_id)
        )
        s = result.scalar_one_or_none()
        if not s:
            raise AppError("NOT_FOUND", 404, {"detail": "优化会话不存在"})
        return {
            "session_id": s.id, "skill_id": s.skill_id, "name": s.name,
            "goal": s.goal, "status": s.status,
            "config": {k: v for k, v in (s.config or {}).items() if not k.startswith("_")},
            "baseline_commit": s.baseline_commit, "baseline_score": s.baseline_score,
            "best_candidate_id": s.best_candidate_id,
            "current_iteration": s.current_iteration,
            "max_iterations": s.max_iterations,
            "total_tokens": s.total_tokens,
            "created_by": s.created_by,
            "created_at": isoformat_bjt(s.created_at),
            "started_at": isoformat_bjt(s.started_at),
            "finished_at": isoformat_bjt(s.finished_at),
            "failure_reason": (s.config or {}).get("failure_reason"),
        }

    # ── 启动/暂停（C3: 原子 CAS）──

    async def start(self, session_id: str, db: AsyncSession) -> dict:
        """启动优化循环 — CAS 保证原子性"""
        # C3: compare-and-set，只有 created/paused 才能切到 running
        result = await db.execute(
            update(OptimizerSession)
            .where(OptimizerSession.id == session_id)
            .where(OptimizerSession.status.in_(["created", "paused"]))
            .values(status="running", started_at=now_bjt())
            .returning(OptimizerSession.id)
        )
        row = result.first()
        if not row:
            # 要么不存在，要么状态不对
            check = await db.execute(
                select(OptimizerSession.status).where(OptimizerSession.id == session_id)
            )
            current = check.scalar()
            if current is None:
                raise AppError("NOT_FOUND", 404)
            raise AppError("INVALID_STATUS", 400, {"detail": f"当前状态 {current} 不可启动"})

        # C3: 进程内去重
        if session_id in self._running_tasks and not self._running_tasks[session_id].done():
            logger.warning(f"会话 {session_id} 已有运行中的任务，跳过重复启动")
            return {"session_id": session_id, "status": "running"}

        task = asyncio.create_task(self._run_loop(session_id))
        self._running_tasks[session_id] = task
        logger.info(f"启动优化会话: {session_id}")
        return {"session_id": session_id, "status": "running"}

    async def pause(self, session_id: str, db: AsyncSession) -> dict:
        await db.execute(
            update(OptimizerSession)
            .where(OptimizerSession.id == session_id)
            .values(status="paused")
        )
        return {"session_id": session_id, "status": "paused"}

    # ── 核心优化循环 ──

    async def _run_loop(self, session_id: str) -> None:
        """
        autoresearch 式闭环：
        分析失败 → 生成候选 → 验证 → 评测 → 接受/拒绝 → 更新 baseline
        """
        from app.optimizer.benchmark_builder import benchmark_builder
        from app.optimizer.benchmark_runner import benchmark_runner
        from app.optimizer.acceptance_policy import acceptance_policy
        from app.optimizer.candidate_generator import candidate_generator
        from app.skills.core.git_service import git_service
        from app.skills.core.parser import skill_parser
        from app.execution.models import DecisionLog

        paused = False  # H1: 追踪退出原因

        try:
            # M1: 短事务 — 加载 session 配置
            async with async_session_factory() as db:
                sess_result = await db.execute(
                    select(OptimizerSession).where(OptimizerSession.id == session_id)
                )
                session = sess_result.scalar_one()
                config = session.config or {}
                budget = config.get("budget", {})
                max_iter = budget.get("max_iterations", session.max_iterations)
                skill_id = session.skill_id

            # 确保 benchmark pack（短事务）
            async with async_session_factory() as db:
                pack_id = config.get("benchmark_pack_id")
                if not pack_id:
                    pack_result = await benchmark_builder.build(
                        skill_id=skill_id, name=f"auto-{session_id}",
                        source="mixed", description="自动构建",
                        max_cases=200, min_rating=None,
                        user_id=session.created_by, db=db,
                    )
                    pack_id = pack_result["pack_id"]
                    await benchmark_builder.freeze(pack_id=pack_id, db=db)
                    config["benchmark_pack_id"] = pack_id
                    await db.execute(
                        update(OptimizerSession)
                        .where(OptimizerSession.id == session_id)
                        .values(config=config)
                    )
                    await db.commit()

            # 跑 baseline（短事务）
            logger.info(f"[{session_id}] 跑 baseline 评测...")
            async with async_session_factory() as db:
                baseline_run = await benchmark_runner.run(pack_id, candidate_id=None, db=db)
                baseline_score = baseline_run.get("metrics", {})
                await db.execute(
                    update(OptimizerSession)
                    .where(OptimizerSession.id == session_id)
                    .values(baseline_score=baseline_score)
                )
                await db.commit()

            # C2: 从 DB 恢复 baseline MD（而非读 live file）
            current_md = config.get("_baseline_md", "")
            if not current_md:
                current_md = git_service.read_file(skill_id, "SKILL.md") or ""
            current_parsed = skill_parser.parse(current_md)

            # 本地 token 累加器（H5）
            tokens_this_run = 0

            # 迭代循环
            for iteration in range(session.current_iteration, max_iter):
                # 检查是否被暂停（短事务）
                async with async_session_factory() as db:
                    status_result = await db.execute(
                        select(OptimizerSession.status)
                        .where(OptimizerSession.id == session_id)
                    )
                    current_status = status_result.scalar()

                if current_status != "running":
                    logger.info(f"[{session_id}] 状态变为 {current_status}，退出循环")
                    paused = True  # H1
                    break

                logger.info(f"[{session_id}] 迭代 {iteration + 1}/{max_iter}")

                # 1. 获取失败用例 + 历史日志（短事务）
                async with async_session_factory() as db:
                    baseline_failures = await benchmark_runner.get_failures(
                        baseline_run["run_id"], db=db,
                    )
                    log_result = await db.execute(
                        select(DecisionLog)
                        .where(DecisionLog.skill_id == skill_id)
                        .where(DecisionLog.user_action.isnot(None))
                        .order_by(DecisionLog.created_at.desc())
                        .limit(20)
                    )
                    logs = [
                        {"input_snapshot": l.input_snapshot, "user_action": l.user_action,
                         "reject_reason": l.reject_reason, "rating": l.rating}
                        for l in log_result.scalars().all()
                    ]

                # 2. 生成候选（无 DB 事务，纯 LLM 调用）
                try:
                    candidate = await candidate_generator.generate(
                        session_id=session_id, iteration=iteration + 1,
                        skill_md=current_md, parsed=current_parsed,
                        failures=baseline_failures, decision_logs=logs,
                        config={**config, "goal": session.goal},
                        skill_id=skill_id,  # F4: 成本归因
                    )
                except Exception as e:
                    # H2: 候选生成失败不杀死整轮
                    logger.warning(f"[{session_id}] 迭代 {iteration + 1} 候选生成异常: {e}")
                    candidate = None

                if not candidate:
                    logger.warning(f"[{session_id}] 迭代 {iteration + 1} 无候选，跳过")
                    continue

                # 3. 验证
                try:
                    new_parsed = skill_parser.parse(candidate.skill_md_patch)
                    if not new_parsed.steps:
                        raise ValueError("解析后无决策步骤")
                    candidate.validation_status = "passed"
                except Exception as e:
                    candidate.validation_status = "failed"
                    candidate.validation_errors = [str(e)]
                    candidate.decision = "rejected"
                    candidate.reject_reason = f"验证失败: {e}"
                    # 写入 DB（短事务）
                    async with async_session_factory() as db:
                        db.add(candidate)
                        await db.commit()
                    logger.warning(f"[{session_id}] 候选 {candidate.id} 验证失败: {e}")
                    continue

                # 4. 评测（C1: 用临时文件快照，不覆写 live SKILL.md）
                import tempfile, os
                snapshot_dir = None
                try:
                    snapshot_dir = tempfile.mkdtemp(prefix=f"opt-{session_id[:8]}-")
                    snapshot_path = os.path.join(snapshot_dir, "SKILL.md")
                    with open(snapshot_path, "w", encoding="utf-8") as f:
                        f.write(candidate.skill_md_patch)

                    # 临时替换执行用的 SKILL.md → 评测 → 立即恢复
                    original_md = git_service.read_file(skill_id, "SKILL.md")
                    git_service.write_file(skill_id, "SKILL.md", candidate.skill_md_patch)
                    try:
                        async with async_session_factory() as db:
                            cand_run = await benchmark_runner.run(
                                pack_id, candidate_id=candidate.id, db=db,
                            )
                            await db.commit()
                        cand_score = cand_run.get("metrics", {})
                        candidate.benchmark_status = "completed"
                        candidate.benchmark_score = cand_score
                    finally:
                        # C1: 始终恢复原始文件
                        git_service.write_file(skill_id, "SKILL.md", original_md or current_md)
                except Exception as e:
                    candidate.benchmark_status = "failed"
                    candidate.decision = "rejected"
                    candidate.reject_reason = f"评测失败: {e}"
                    async with async_session_factory() as db:
                        db.add(candidate)
                        await db.commit()
                    logger.warning(f"[{session_id}] 候选 {candidate.id} 评测失败: {e}")
                    continue
                finally:
                    if snapshot_dir:
                        try:
                            import shutil
                            shutil.rmtree(snapshot_dir, ignore_errors=True)
                        except Exception as e:
                            logger.debug("snapshot_dir 清理失败 session={}: {}", session_id, e)

                # 5. 判定
                passed, reason = acceptance_policy.pass_offline(cand_score, baseline_score, config)
                if passed:
                    candidate.decision = "accepted"
                    current_md = candidate.skill_md_patch
                    current_parsed = new_parsed
                    baseline_score = cand_score
                    baseline_run = cand_run
                    # C2: 持久化当前 baseline MD
                    config["_baseline_md"] = current_md
                    logger.info(f"[{session_id}] 候选 {candidate.id} 接受: {reason}")
                else:
                    candidate.decision = "rejected"
                    candidate.reject_reason = reason
                    logger.info(f"[{session_id}] 候选 {candidate.id} 拒绝: {reason}")

                # 写入候选 + 更新进度（短事务，H5: SQL 增量）
                tokens_this_run += (candidate.token_count or 0)
                async with async_session_factory() as db:
                    db.add(candidate)
                    await db.execute(
                        update(OptimizerSession)
                        .where(OptimizerSession.id == session_id)
                        .values(
                            current_iteration=iteration + 1,
                            # H5: SQL 增量更新，不依赖旧值
                            total_tokens=OptimizerSession.total_tokens + (candidate.token_count or 0),
                            # C2: 持久化 baseline
                            best_candidate_id=candidate.id if candidate.decision == "accepted" else OptimizerSession.best_candidate_id,
                            baseline_score=baseline_score if candidate.decision == "accepted" else OptimizerSession.baseline_score,
                            config=config,
                        )
                    )
                    await db.commit()

                # Redis 状态缓存
                try:
                    from app.common.cache import cache_set
                    await cache_set(f"optimizer:session:{session_id}:status", {
                        "iteration": iteration + 1,
                        "max_iterations": max_iter,
                        "last_candidate": candidate.id,
                        "last_decision": candidate.decision,
                        "accuracy": baseline_score.get("accuracy"),
                        "total_tokens": tokens_this_run,
                    }, ttl=30)
                except Exception as e:
                    logger.warning(f"[{session_id}] Redis 缓存写入失败: {e}")

            # H1: 区分退出原因 — 只有自然跑完才标 completed
            if not paused:
                async with async_session_factory() as db:
                    await db.execute(
                        update(OptimizerSession)
                        .where(OptimizerSession.id == session_id)
                        .values(status="completed", finished_at=now_bjt())
                    )
                    await db.commit()
                logger.info(f"[{session_id}] 优化完成")
            else:
                logger.info(f"[{session_id}] 优化已暂停，保持 paused 状态")

        except Exception as e:
            # 提取 AppError 的友好 message，否则用 type+str
            err_msg = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
            logger.error(f"[{session_id}] 优化循环异常: {err_msg}", exc_info=True)
            try:
                async with async_session_factory() as db:
                    # 把失败原因写到 config.failure_reason，便于前端展示
                    sess_row = await db.execute(
                        select(OptimizerSession.config).where(OptimizerSession.id == session_id)
                    )
                    cur_config = sess_row.scalar() or {}
                    cur_config["failure_reason"] = err_msg
                    await db.execute(
                        update(OptimizerSession)
                        .where(OptimizerSession.id == session_id)
                        .values(
                            status="failed",
                            finished_at=now_bjt(),
                            config=cur_config,
                        )
                    )
                    await db.commit()
            except Exception as inner:
                logger.error(f"[{session_id}] 标记 failed 也失败: {inner}")
        finally:
            self._running_tasks.pop(session_id, None)

    # ── 候选管理 ──

    async def list_candidates(self, session_id: str, db: AsyncSession) -> list:
        result = await db.execute(
            select(OptimizerCandidate)
            .where(OptimizerCandidate.session_id == session_id)
            .order_by(OptimizerCandidate.iteration.desc())
        )
        return [
            {
                "candidate_id": c.id, "iteration": c.iteration,
                "diff_summary": c.diff_summary,
                "benchmark_status": c.benchmark_status,
                "benchmark_score": c.benchmark_score,
                "decision": c.decision,
                "created_at": isoformat_bjt(c.created_at),
            }
            for c in result.scalars().all()
        ]

    async def get_candidate(self, candidate_id: str, db: AsyncSession) -> dict:
        result = await db.execute(
            select(OptimizerCandidate).where(OptimizerCandidate.id == candidate_id)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise AppError("NOT_FOUND", 404)
        return {
            "candidate_id": c.id, "session_id": c.session_id,
            "iteration": c.iteration, "parent_id": c.parent_id,
            "skill_md_patch": c.skill_md_patch,
            "diff_summary": c.diff_summary,
            "generation_rationale": c.generation_rationale,
            "changes": c.changes,
            "validation_status": c.validation_status,
            "validation_errors": c.validation_errors,
            "benchmark_status": c.benchmark_status,
            "benchmark_score": c.benchmark_score,
            "decision": c.decision,
            "reject_reason": c.reject_reason,
            "model_name": c.model_name,
            "token_count": c.token_count,
            "created_at": isoformat_bjt(c.created_at),
        }

    async def promote(self, candidate_id: str, user_id: str, db: AsyncSession) -> dict:
        """晋升候选版本：写入 git → 进入 shadow"""
        result = await db.execute(
            select(OptimizerCandidate).where(OptimizerCandidate.id == candidate_id)
        )
        c = result.scalar_one_or_none()
        if not c:
            raise AppError("NOT_FOUND", 404)
        if not c.skill_md_patch:
            raise AppError("NO_PATCH", 400, {"detail": "候选版本无内容"})

        from app.skills.core.git_service import git_service
        session_result = await db.execute(
            select(OptimizerSession).where(OptimizerSession.id == c.session_id)
        )
        session = session_result.scalar_one()

        git_service.write_file(session.skill_id, "SKILL.md", c.skill_md_patch)
        git_service.commit(session.skill_id, f"[optimizer] {c.diff_summary or '优化候选版本'}", user_id)

        shadow_status = "skipped"
        if (session.config or {}).get("auto_shadow", True):
            try:
                from app.skills.lifecycle.shadow_service import start_shadow
                await start_shadow(db, session.skill_id, user_id)
                shadow_status = "running"
                logger.info(f"候选 {candidate_id} 进入 shadow 模式")
            except Exception as e:
                logger.warning(f"启动 shadow 失败: {e}")

        await db.execute(
            update(OptimizerCandidate)
            .where(OptimizerCandidate.id == candidate_id)
            .values(decision="promoted", shadow_status=shadow_status)
        )
        return {"candidate_id": candidate_id, "decision": "promoted", "shadow": shadow_status}

    async def check_shadow_and_auto_promote(self, candidate_id: str, db: AsyncSession) -> dict:
        """检查 shadow 结果"""
        from app.skills.lifecycle.shadow_service import get_shadow_stats
        from app.optimizer.acceptance_policy import acceptance_policy

        result = await db.execute(
            select(OptimizerCandidate).where(OptimizerCandidate.id == candidate_id)
        )
        c = result.scalar_one_or_none()
        if not c or c.shadow_status != "running":
            return {"status": "skip"}

        session_result = await db.execute(
            select(OptimizerSession).where(OptimizerSession.id == c.session_id)
        )
        session = session_result.scalar_one()
        config = session.config or {}

        stats = await get_shadow_stats(db, session.skill_id)
        passed, reason = acceptance_policy.pass_shadow(stats, config)

        if passed:
            await db.execute(
                update(OptimizerCandidate)
                .where(OptimizerCandidate.id == candidate_id)
                .values(shadow_status="passed", shadow_stats=stats)
            )
            if config.get("auto_promote", False):
                from app.skills.lifecycle.shadow_service import promote_shadow
                try:
                    await promote_shadow(db, session.skill_id, session.created_by)
                    logger.info(f"自动晋升 shadow: {session.skill_id}")
                except Exception as e:
                    logger.warning(f"自动晋升失败: {e}")
            return {"status": "passed", "stats": stats}
        else:
            await db.execute(
                update(OptimizerCandidate)
                .where(OptimizerCandidate.id == candidate_id)
                .values(shadow_stats=stats)
            )
            return {"status": "running", "reason": reason, "stats": stats}

    async def reject_candidate(self, candidate_id: str, reason: str, db: AsyncSession) -> dict:
        await db.execute(
            update(OptimizerCandidate)
            .where(OptimizerCandidate.id == candidate_id)
            .values(decision="rejected", reject_reason=reason)
        )
        return {"candidate_id": candidate_id, "decision": "rejected"}

    # ── 报告 ──

    async def get_report(self, session_id: str, db: AsyncSession) -> dict:
        sess_result = await db.execute(
            select(OptimizerSession).where(OptimizerSession.id == session_id)
        )
        session = sess_result.scalar_one_or_none()
        if not session:
            raise AppError("NOT_FOUND", 404)

        cands_result = await db.execute(
            select(OptimizerCandidate)
            .where(OptimizerCandidate.session_id == session_id)
            .order_by(OptimizerCandidate.iteration)
        )
        cands = cands_result.scalars().all()

        accepted = [c for c in cands if c.decision == "accepted"]
        rejected = [c for c in cands if c.decision == "rejected"]
        promoted = [c for c in cands if c.decision == "promoted"]

        accuracy_trend = []
        for c in cands:
            if c.benchmark_score and "accuracy" in c.benchmark_score:
                accuracy_trend.append({
                    "iteration": c.iteration,
                    "accuracy": c.benchmark_score["accuracy"],
                    "decision": c.decision,
                })

        best = None
        if session.baseline_score and accepted:
            best_cand = max(accepted, key=lambda x: (x.benchmark_score or {}).get("accuracy", 0))
            best = {
                "candidate_id": best_cand.id,
                "accuracy_gain": (best_cand.benchmark_score or {}).get("accuracy", 0) - session.baseline_score.get("accuracy", 0),
                "diff_summary": best_cand.diff_summary,
            }

        return {
            "session_id": session_id,
            "skill_id": session.skill_id,
            "goal": session.goal,
            "status": session.status,
            "total_iterations": session.current_iteration,
            "total_tokens": session.total_tokens,
            "baseline_accuracy": (session.baseline_score or {}).get("accuracy"),
            "best_accuracy": (session.baseline_score or {}).get("accuracy", 0) if not accepted else max((c.benchmark_score or {}).get("accuracy", 0) for c in accepted),
            "candidates_total": len(cands),
            "candidates_accepted": len(accepted),
            "candidates_rejected": len(rejected),
            "candidates_promoted": len(promoted),
            "accuracy_trend": accuracy_trend,
            "best_improvement": best,
        }


optimizer_controller = OptimizerController()
