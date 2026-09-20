"""
历史回放：读取decision_log中的历史数据，用新参数重跑，对比差异。

支持两种模式：
- 在线模式：OpenClaw可用时，真实重新执行并对比结果
- 离线模式：OpenClaw不可用时，仅展示参数差异（dry-run）
"""

from datetime import datetime

from loguru import logger
from sqlalchemy import select, and_

from app.common.time_utils import parse_bjt_datetime
from app.database import async_session_factory
from app.execution.models import DecisionLog
from app.execution.openclaw_client import default_client


def _parse_range_start(value: str) -> datetime:
    return parse_bjt_datetime(value)


def _parse_range_end(value: str) -> datetime:
    parsed = parse_bjt_datetime(value)
    if "T" not in value and " " not in value:
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


class ReplayService:
    """历史回放服务：重跑历史决策，对比参数/输出差异"""

    async def run_replay(
        self,
        skill_id: str,
        date_from: str,
        date_to: str,
        new_params: dict | None = None,
    ) -> dict:
        """
        历史回放核心流程：
        1. 从decision_log查询日期范围内的非sandbox执行记录
        2. 逐条重跑：合并原始参数与new_params，调OpenClaw
        3. 对比原始输出与新输出，生成差异摘要
        """
        # 校验日期格式
        try:
            dt_from = _parse_range_start(date_from)
            dt_to = _parse_range_end(date_to)
        except ValueError:
            return {"error": "日期格式错误，请使用YYYY-MM-DD", "records": [], "summary": ""}

        if dt_from > dt_to:
            return {"error": "开始日期不能晚于结束日期", "records": [], "summary": ""}

        # 查询历史决策记录（仅非sandbox的）
        async with async_session_factory() as session:
            result = await session.execute(
                select(DecisionLog)
                .where(and_(
                    DecisionLog.skill_id == skill_id,
                    DecisionLog.created_at >= dt_from,
                    DecisionLog.created_at <= dt_to,
                    DecisionLog.is_sandbox == False,  # noqa: E712
                ))
                .order_by(DecisionLog.created_at)
            )
            logs = result.scalars().all()

        if not logs:
            return {
                "skill_id": skill_id,
                "date_from": date_from,
                "date_to": date_to,
                "records": [],
                "changed_count": 0,
                "total_count": 0,
                "summary": f"在 {date_from} 至 {date_to} 期间无执行记录",
                "mode": "none",
            }

        # 检查OpenClaw是否在线
        openclaw_online = await self._check_openclaw()

        records = []
        changed_count = 0

        for log in logs:
            original_input = log.input_snapshot or {}
            original_output = log.output_result or {}

            # 构造回放输入：原始参数 + 新参数覆盖
            replay_input = {**original_input}
            if new_params:
                replay_input.update(new_params)

            # 计算参数差异（不管是否在线都可以展示）
            param_diff = self._compare_params(original_input, new_params or {})

            if openclaw_online and new_params:
                # 在线模式：真实重跑
                try:
                    replay_output = await default_client.run_skill(
                        skill_id, params=replay_input, sandbox=True,
                    )
                    output_diff = self._compare_outputs(original_output, replay_output)
                    mode = "live"
                except Exception as e:
                    logger.warning(f"回放执行失败: {skill_id} log_id={log.id} error={e}")
                    replay_output = None
                    output_diff = {"changed_fields": [], "summary": "执行失败，无法对比"}
                    mode = "error"
            else:
                # 离线模式：仅展示参数差异，不真实执行
                replay_output = None
                output_diff = {"changed_fields": [], "summary": "离线模式，仅展示参数差异"}
                mode = "dry-run"

            # 判断是否有实质变化
            has_change = bool(param_diff["changed_fields"]) or (
                mode == "live" and bool(output_diff["changed_fields"])
            )
            if has_change:
                changed_count += 1

            record = {
                "log_id": log.id,
                "run_id": log.run_id,
                "date": log.created_at.strftime("%Y-%m-%d %H:%M") if log.created_at else "",
                "original_input": original_input,
                "original_output": original_output,
                "original_output_summary": _summarize(original_output),
                "replay_input": replay_input if new_params else None,
                "replay_output": replay_output,
                "replay_output_summary": _summarize(replay_output) if replay_output else "-",
                "param_diff": param_diff,
                "output_diff": output_diff,
                "has_change": has_change,
                "mode": mode,
                "approval_level": log.approval_level,
            }
            records.append(record)

        total = len(records)
        return {
            "skill_id": skill_id,
            "date_from": date_from,
            "date_to": date_to,
            "records": records,
            "changed_count": changed_count,
            "total_count": total,
            "summary": f"共{total}条决策记录，{changed_count}条会发生变化",
            "mode": "live" if openclaw_online and new_params else "dry-run",
        }

    def _compare_params(self, original: dict, new_params: dict) -> dict:
        """
        对比原始参数与新参数的差异。
        返回：哪些字段被覆盖了、新值是什么。
        """
        if not new_params:
            return {"changed_fields": [], "details": [], "summary": "未提供新参数"}

        changed_fields = []
        details = []

        for key, new_val in new_params.items():
            old_val = original.get(key)
            if old_val != new_val:
                changed_fields.append(key)
                details.append({
                    "field": key,
                    "old_value": old_val,
                    "new_value": new_val,
                    "is_new_field": key not in original,
                })

        return {
            "changed_fields": changed_fields,
            "details": details,
            "summary": f"{len(changed_fields)}个参数发生变化" if changed_fields else "参数无变化",
        }

    def _compare_outputs(self, original: dict, replayed: dict) -> dict:
        """
        逐字段对比原始输出与回放输出的差异。
        返回：变化的字段列表、差异详情、摘要。
        """
        if not original or not replayed:
            return {"changed_fields": [], "details": [], "summary": "输出数据不完整，无法对比"}

        # 收集所有字段（两边的并集）
        all_keys = set(list(original.keys()) + list(replayed.keys()))

        changed_fields = []
        details = []

        for key in sorted(all_keys):
            old_val = original.get(key)
            new_val = replayed.get(key)

            if old_val != new_val:
                changed_fields.append(key)
                detail = {
                    "field": key,
                    "old_value": old_val,
                    "new_value": new_val,
                }
                # 标记变化类型
                if key not in original:
                    detail["change_type"] = "added"
                elif key not in replayed:
                    detail["change_type"] = "removed"
                else:
                    detail["change_type"] = "modified"
                details.append(detail)

        total_fields = len(all_keys)
        changed = len(changed_fields)
        summary = (
            f"{changed}/{total_fields}个字段发生变化"
            if changed > 0
            else "输出完全一致"
        )

        return {
            "changed_fields": changed_fields,
            "details": details,
            "summary": summary,
        }

    async def _check_openclaw(self) -> bool:
        """检查OpenClaw Gateway是否在线"""
        try:
            status = await default_client.get_status()
            return status.get("online", False)
        except Exception:
            return False


def _summarize(output: dict | None) -> str:
    """简短总结输出内容（用于表格展示）"""
    if not output:
        return "-"
    if isinstance(output, dict):
        if "error" in output:
            return f'错误: {str(output["error"])[:30]}'
        if "status" in output:
            return f'status={output["status"]}'
        if "mock" in output:
            return "mock执行"
        # 取前几个key做摘要
        keys = list(output.keys())[:3]
        parts = []
        for k in keys:
            v = output[k]
            v_str = str(v)
            if len(v_str) > 20:
                v_str = v_str[:20] + "..."
            parts.append(f"{k}={v_str}")
        return ", ".join(parts)
    return str(output)[:50]


# 全局服务实例
replay_service = ReplayService()
