"""
Playbook执行引擎：加载YAML → 拓扑排序 → 按依赖顺序执行每个Skill步骤。
SkillForge不直接执行Skill脚本，通过ExecutionService调度OpenClaw完成。
"""

import asyncio
import logging
import re
import time
from collections import deque
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from app.common.audit import audit
from app.common.exceptions import AppError
from app.execution.execution_service import execution_service
from app.execution.models import ExecutionRun, ExecutionStep
from app.playbooks.live import event_bus
from app.common.time_utils import now_bjt

logger = logging.getLogger(__name__)


def _sf():
    """延迟获取async_session_factory，避免测试中engine替换问题"""
    from app.database import async_session_factory
    return async_session_factory


class PlaybookExecutor:
    """Playbook执行引擎，按DAG依赖顺序调度多个Skill"""

    async def run(
        self,
        playbook_name: str,
        params: dict | None = None,
        sandbox: bool = False,
        triggered_by: str = "manual",
    ) -> dict:
        """
        执行Playbook完整流程：
        1. 加载Playbook YAML
        2. 拓扑排序steps（按depends_on）
        3. 创建ExecutionRun记录（playbook_id=name）
        4. 按顺序执行每个step
        5. 返回整体结果
        """
        from app.playbooks.service import get_playbook

        params = params or {}

        # 1. 加载Playbook
        playbook_data = await get_playbook(playbook_name)
        steps = playbook_data.get("steps", [])
        if not steps:
            raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "Playbook没有定义任何步骤"})

        # 2. 拓扑排序
        sorted_steps = self._topological_sort(steps)

        # 3. 创建ExecutionRun
        run_id = str(uuid4())
        async with _sf()() as session:
            run = ExecutionRun(
                id=run_id,
                playbook_id=playbook_name,
                trigger_type=triggered_by,
                started_at=now_bjt(),
                status="running",
                total_steps=len(sorted_steps),
                completed_steps=0,
            )
            session.add(run)
            await session.commit()

        await audit.log(
            "system", "playbook.start", "playbook", playbook_name,
            detail={"run_id": run_id, "sandbox": sandbox, "steps_count": len(sorted_steps)},
        )

        # 4. 按顺序执行每个step
        step_outputs: dict[str, dict] = {}  # step_id → 执行结果
        step_statuses: dict[str, str] = {}  # step_id → 状态
        completed_count = 0
        overall_status = "completed"

        for order_idx, step in enumerate(sorted_steps):
            step_id = step["id"]
            skill_id = step.get("skill_id") or step.get("skill", "")
            timeout_sec = step.get("timeout", 300)
            on_failure = step.get("on_failure", "terminate")

            # 4a. 检查depends_on条件
            deps_met, skip_reason = self._check_dependencies(step, step_outputs, step_statuses)

            if not deps_met:
                # 依赖不满足，跳过此step
                logger.info("Playbook步骤 %s 依赖不满足，跳过: %s", step_id, skip_reason)
                step_result = {
                    "status": "skipped",
                    "reason": skip_reason,
                    "output": {},
                }
                step_outputs[step_id] = step_result
                step_statuses[step_id] = "skipped"

                # 记录ExecutionStep
                await self._record_step(
                    run_id, step_id, skill_id, order_idx, "skipped",
                    input_data=params, output_data=None, duration_ms=0,
                    error_message=skip_reason,
                )
                await event_bus.publish(run_id, {
                    "type": "step_status", "step_id": step_id, "status": "skipped",
                })
                continue

            # 4b. 广播 running 状态
            await event_bus.publish(run_id, {
                "type": "step_status", "step_id": step_id, "status": "running",
            })

            # 4c-d. 执行step
            step_result = await self._execute_step(
                step, step_outputs, run_id, sandbox, params, order_idx, timeout_sec,
            )

            step_outputs[step_id] = step_result
            step_statuses[step_id] = step_result["status"]

            # 广播步骤完成状态
            await event_bus.publish(run_id, {
                "type": "step_status",
                "step_id": step_id,
                "status": step_result["status"],
                "output": step_result.get("output"),
            })

            if step_result["status"] in ("completed", "success"):
                completed_count += 1
            else:
                # 4e. 失败处理
                if on_failure == "terminate":
                    logger.warning(
                        "Playbook步骤 %s 失败，策略为terminate，终止执行", step_id,
                    )
                    overall_status = "failed"
                    break
                elif on_failure == "retry":
                    # 重试一次
                    logger.info("Playbook步骤 %s 失败，重试一次", step_id)
                    step_result = await self._execute_step(
                        step, step_outputs, run_id, sandbox, params, order_idx, timeout_sec,
                    )
                    step_outputs[step_id] = step_result
                    step_statuses[step_id] = step_result["status"]

                    if step_result["status"] in ("completed", "success"):
                        completed_count += 1
                    else:
                        # 重试也失败，终止
                        overall_status = "failed"
                        break
                else:
                    # skip：记录失败但继续
                    logger.info("Playbook步骤 %s 失败，策略为skip，继续执行", step_id)

        # 5. 更新ExecutionRun状态
        async with _sf()() as session:
            result = await session.execute(
                select(ExecutionRun).where(ExecutionRun.id == run_id)
            )
            run = result.scalar_one()
            run.status = overall_status
            run.completed_at = now_bjt()
            run.completed_steps = completed_count
            await session.commit()

        await audit.log(
            "system", "playbook.complete", "playbook", playbook_name,
            detail={
                "run_id": run_id,
                "status": overall_status,
                "completed_steps": completed_count,
                "total_steps": len(sorted_steps),
            },
        )

        # 广播运行结束
        await event_bus.publish(run_id, {
            "type": "run_complete",
            "status": overall_status,
            "completed_steps": completed_count,
            "total_steps": len(sorted_steps),
        })

        return {
            "run_id": run_id,
            "playbook": playbook_name,
            "status": overall_status,
            "total_steps": len(sorted_steps),
            "completed_steps": completed_count,
            "steps": step_outputs,
        }

    def _topological_sort(self, steps: list[dict]) -> list[dict]:
        """
        拓扑排序，确保依赖先执行。
        使用Kahn算法（BFS），依赖少的先执行。
        """
        # 构建依赖图
        step_map: dict[str, dict] = {}
        in_degree: dict[str, int] = {}
        adj: dict[str, list[str]] = {}  # dep → [依赖dep的step]

        for step in steps:
            sid = step.get("id", "")
            step_map[sid] = step
            in_degree[sid] = 0
            adj.setdefault(sid, [])

        for step in steps:
            sid = step.get("id", "")
            deps = self._extract_dep_ids(step)
            in_degree[sid] = len(deps)
            for dep_id in deps:
                adj.setdefault(dep_id, []).append(sid)

        # BFS
        queue = deque([sid for sid, deg in in_degree.items() if deg == 0])
        sorted_ids: list[str] = []

        while queue:
            node = queue.popleft()
            sorted_ids.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_ids) != len(steps):
            raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "Playbook存在循环依赖"})

        return [step_map[sid] for sid in sorted_ids]

    def _extract_dep_ids(self, step: dict) -> list[str]:
        """
        从step的depends_on中提取依赖的step_id列表。
        支持两种格式：
        - 字符串列表: ["step_1", "step_2"]
        - 字典列表: [{"step_id": "step_1", "condition": "..."}, ...]
        - 单个字符串: "step_1"
        """
        deps = step.get("depends_on", [])
        if isinstance(deps, str):
            return [deps]
        if not isinstance(deps, list):
            return []

        result: list[str] = []
        for dep in deps:
            if isinstance(dep, str):
                result.append(dep)
            elif isinstance(dep, dict):
                dep_id = dep.get("step_id", "")
                if dep_id:
                    result.append(dep_id)
        return result

    def _check_dependencies(
        self,
        step: dict,
        step_outputs: dict[str, dict],
        step_statuses: dict[str, str],
    ) -> tuple[bool, str]:
        """
        检查step的所有依赖是否满足。
        返回 (是否满足, 不满足的原因)。
        """
        deps = step.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if not deps:
            return True, ""

        for dep in deps:
            if isinstance(dep, str):
                # 简单依赖：只需前置step已执行完成
                dep_id = dep
                condition = None
            elif isinstance(dep, dict):
                dep_id = dep.get("step_id", "")
                condition = dep.get("condition")
            else:
                continue

            # 检查依赖step是否已执行
            if dep_id not in step_statuses:
                return False, f"依赖步骤 {dep_id} 尚未执行"

            dep_status = step_statuses[dep_id]
            if dep_status == "failed":
                return False, f"依赖步骤 {dep_id} 执行失败"

            # 如果有条件表达式，评估条件
            if condition:
                if not self._evaluate_condition(condition, step_outputs):
                    return False, f"依赖步骤 {dep_id} 的条件不满足: {condition}"

        return True, ""

    def _evaluate_condition(self, condition: str, step_outputs: dict[str, dict]) -> bool:
        """
        安全评估条件表达式，不使用eval。
        支持的格式：
        - "steps.step_1.output.has_red == true"
        - "steps.step_1.output.red_count > 0"
        - "steps.step_1.output.status == completed"
        - "steps.step_1.output.score >= 80"

        路径解析：steps.<step_id>.output.<field_path>
        运算符：==, !=, >, <, >=, <=
        值类型：true/false（布尔）、数字、字符串
        """
        # 匹配 "路径 运算符 值" 格式
        pattern = r"^([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+)$"
        match = re.match(pattern, condition.strip())
        if not match:
            logger.warning("无法解析条件表达式: %s，默认为True", condition)
            return True

        path_str = match.group(1)
        operator = match.group(2)
        expected_str = match.group(3).strip().strip('"').strip("'")

        # 解析路径取值
        actual_value = self._resolve_path(path_str, step_outputs)

        # 解析期望值
        expected_value = self._parse_value(expected_str)

        # 比较
        return self._compare(actual_value, operator, expected_value)

    def _resolve_path(self, path: str, step_outputs: dict[str, dict]) -> object:
        """
        解析点分路径，从step_outputs中取值。
        路径格式：steps.<step_id>.output.<field>.<sub_field>...
        或者简写：<step_id>.<field>
        """
        parts = path.split(".")

        # 标准格式：steps.step_1.output.xxx
        if len(parts) >= 3 and parts[0] == "steps":
            step_id = parts[1]
            # 跳过 "output" 层（如果有的话）
            if len(parts) >= 4 and parts[2] == "output":
                field_parts = parts[3:]
            else:
                field_parts = parts[2:]
        else:
            # 不认识的路径格式，返回None
            logger.warning("无法解析路径: %s", path)
            return None

        # 从step_outputs中取值
        step_data = step_outputs.get(step_id)
        if step_data is None:
            return None

        # 在output字段中逐层取值
        output = step_data.get("output", step_data)
        current = output
        for part in field_parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
            if current is None:
                return None

        return current

    def _parse_value(self, value_str: str) -> object:
        """将字符串解析为Python值（布尔、数字、字符串）"""
        lower = value_str.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        if lower == "none" or lower == "null":
            return None

        # 尝试解析为数字
        try:
            if "." in value_str:
                return float(value_str)
            return int(value_str)
        except ValueError:
            pass

        # 原样返回字符串
        return value_str

    def _compare(self, actual: object, operator: str, expected: object) -> bool:
        """安全比较两个值"""
        try:
            if operator == "==":
                # 布尔值的字符串比较兼容
                if isinstance(actual, bool) or isinstance(expected, bool):
                    return bool(actual) == bool(expected)
                return actual == expected
            elif operator == "!=":
                if isinstance(actual, bool) or isinstance(expected, bool):
                    return bool(actual) != bool(expected)
                return actual != expected
            elif operator == ">":
                return float(actual) > float(expected)
            elif operator == "<":
                return float(actual) < float(expected)
            elif operator == ">=":
                return float(actual) >= float(expected)
            elif operator == "<=":
                return float(actual) <= float(expected)
        except (TypeError, ValueError):
            logger.warning("条件比较失败: actual=%s op=%s expected=%s", actual, operator, expected)
            return False
        return False

    async def _execute_step(
        self,
        step: dict,
        step_outputs: dict[str, dict],
        run_id: str,
        sandbox: bool,
        global_params: dict,
        order_idx: int,
        timeout_sec: int,
    ) -> dict:
        """
        执行单个Playbook步骤：
        - 合并参数
        - 调用execution_service
        - 记录ExecutionStep
        - 处理超时
        """
        step_id = step["id"]
        skill_id = step.get("skill_id") or step.get("skill", "")
        params_override = step.get("params_override", {}) or {}

        # 合并参数：全局参数 + 步骤覆盖参数
        merged_params = {**global_params, **params_override}

        start_time = time.monotonic()

        try:
            # 使用asyncio.wait_for处理超时
            result = await asyncio.wait_for(
                execution_service.execute_skill(
                    skill_id=skill_id,
                    params=merged_params,
                    sandbox=sandbox,
                    triggered_by=f"playbook:{run_id}",
                ),
                timeout=timeout_sec,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            step_result = {
                "status": "completed",
                "output": result.get("output", {}),
                "run_id": result.get("run_id"),
            }

            # 记录ExecutionStep
            await self._record_step(
                run_id, step_id, skill_id, order_idx, "completed",
                input_data=merged_params,
                output_data=result.get("output", {}),
                duration_ms=duration_ms,
            )

            return step_result

        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.error("Playbook步骤 %s 超时（%ds）", step_id, timeout_sec)

            await self._record_step(
                run_id, step_id, skill_id, order_idx, "timeout",
                input_data=merged_params,
                duration_ms=duration_ms,
                error_message=f"执行超时（{timeout_sec}秒）",
            )

            return {
                "status": "timeout",
                "output": {},
                "error": f"执行超时（{timeout_sec}秒）",
            }

        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            error_msg = str(e)
            logger.error("Playbook步骤 %s 执行失败: %s", step_id, error_msg)

            await self._record_step(
                run_id, step_id, skill_id, order_idx, "failed",
                input_data=merged_params,
                duration_ms=duration_ms,
                error_message=error_msg,
            )

            return {
                "status": "failed",
                "output": {},
                "error": error_msg,
            }

    async def _record_step(
        self,
        run_id: str,
        step_id: str,
        skill_id: str,
        order_idx: int,
        status: str,
        input_data: dict | None = None,
        output_data: dict | None = None,
        duration_ms: int = 0,
        error_message: str | None = None,
    ) -> None:
        """将单个步骤的执行结果写入execution_steps表"""
        async with _sf()() as session:
            exec_step = ExecutionStep(
                run_id=run_id,
                skill_id=skill_id,
                step_order=order_idx,
                status=status,
                input_data=input_data,
                output_data=output_data,
                started_at=now_bjt(),
                completed_at=now_bjt(),
                duration_ms=duration_ms,
                error_message=error_message,
            )
            session.add(exec_step)
            await session.commit()


# 全局实例
playbook_executor = PlaybookExecutor()
