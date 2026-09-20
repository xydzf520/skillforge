"""Workbench 会话消息持久化服务。

将 AI 对话消息写入 skill_workbench_messages 表，
支持跨刷新恢复对话历史。
"""

from __future__ import annotations

import hashlib
import asyncio
from datetime import datetime

from loguru import logger
from sqlalchemy import desc, select

from app.common.time_utils import isoformat_bjt, now_bjt
from app.workbench.models import SkillWorkbenchMessage, SkillWorkbenchSession


ERROR_ATTRIBUTIONS = {
    "STREAM_INCOMPLETE": {
        "reason": "AI 输出流中断，服务端没有收到完成事件。",
        "suggestion": "请重新发送消息；如果刚才已经改了文件，先在历史里查看最近一次 AI 回合和 Git 提交。",
    },
    "PERMISSION_DENIED": {
        "reason": "权限被拒绝，AI 助手没有被允许执行这一步。",
        "suggestion": "请确认权限请求，或改用当前账号有权限的文件和命令。",
    },
    "FILE_NOT_FOUND": {
        "reason": "目标文件不存在或已被移动。",
        "suggestion": "请刷新文件列表，确认路径后重试。",
    },
    "API_FAILURE": {
        "reason": "外部 API 或 AI 服务调用失败。",
        "suggestion": "请检查 API key、模型、base_url 或目标接口可用性后重试。",
    },
    "FIXTURE_SAMPLE_MISUSE": {
        "reason": "运行时疑似把 fixture/sample 当成真实数据。",
        "suggestion": "请改为从 payload 或 SkillForge SDK 获取真实数据，fixtures/sample_input.json 只用于离线契约校验。",
    },
}


def attribute_ai_error(code: str | None = None, error: str | None = None) -> dict:
    raw_code = str(code or "")
    raw = str(error or "")
    lower = raw.lower()
    if "stream_incomplete" in raw_code.lower() or raw_code == "CODING_AGENT_STREAM_INCOMPLETE":
        key = "STREAM_INCOMPLETE"
    elif "permission" in raw_code.lower() or "permission denied" in lower or "权限" in raw or "denied" in lower:
        key = "PERMISSION_DENIED"
    elif "not_found" in raw_code.lower() or "no such file" in lower or "file not found" in lower or "不存在" in raw:
        key = "FILE_NOT_FOUND"
    elif "fixture" in lower or "sample_input" in lower or "样例" in raw or "假数据" in raw:
        key = "FIXTURE_SAMPLE_MISUSE"
    elif "api" in raw_code.lower() or "api" in lower or "base_url" in lower or "key" in lower or "timeout" in lower or "连接" in raw:
        key = "API_FAILURE"
    else:
        key = raw_code or "UNKNOWN"
    mapped = ERROR_ATTRIBUTIONS.get(key, {
        "reason": raw or "AI 会话失败。",
        "suggestion": "请查看错误详情后重试；如果持续失败，请联系管理员。",
    })
    return {"key": key, **mapped}


def score_ai_turn_quality(turn: dict) -> dict:
    changed = bool(turn.get("changed_files"))
    ran = bool(turn.get("ran") or turn.get("ran_tests") or turn.get("ran_samples"))
    real_data = bool(turn.get("used_real_data"))
    reports_todos = bool(turn.get("generated_reports") or turn.get("generated_todos"))
    committed = bool(turn.get("git_commit") or turn.get("git_commit_full"))
    items = [
        ("changed_files", "改文件", changed, 25),
        ("ran", "运行/验证", ran, 20),
        ("real_data", "真实取数", real_data, 20),
        ("reports_todos", "reports/todos", reports_todos, 15),
        ("git_commit", "Git commit", committed, 20),
    ]
    score = sum(weight for _, _, passed, weight in items if passed)
    return {
        "score": score,
        "level": "good" if score >= 80 else "warn" if score >= 60 else "risk",
        "items": [{"key": key, "label": label, "passed": passed, "weight": weight} for key, label, passed, weight in items],
        "summary": f"AI 输出质量 {score}/100",
    }


def _sf():
    from app.database import async_session_factory
    return async_session_factory


def build_coding_session_id(skill_id: str, user_id: str) -> str:
    """生成稳定的 coding 临时 session id。"""
    digest = hashlib.sha1(f"{skill_id}:{user_id}".encode("utf-8")).hexdigest()[:16]
    return f"coding-{digest}"


def _hashed_session_id(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"coding-{digest}"


def _event_type(message: SkillWorkbenchMessage) -> str:
    intent = message.intent_json if isinstance(message.intent_json, dict) else {}
    if isinstance(intent.get("type"), str):
        return intent["type"]
    content = message.content or ""
    if content.startswith("tool_call:"):
        return "tool_call"
    if content.startswith("tool_result:"):
        return "tool_result"
    if content.startswith("file_change:"):
        return "file_change"
    if content in {"done", "error", "git_commit", "usage", "session_ready", "quality_summary"}:
        return content
    return ""


def _strip_skill_prefix(path: str, skill_id: str) -> str:
    normalized = str(path or "").replace("\\", "/").lstrip("/")
    prefix = f"{skill_id}/"
    if normalized.startswith(prefix):
        return normalized[len(prefix):]
    return normalized


def _append_unique_file(files: list[str], path: str, skill_id: str) -> None:
    clean_path = _strip_skill_prefix(path, skill_id).strip()
    if clean_path and clean_path not in files:
        files.append(clean_path)


async def _diff_summary_for_commit(skill_id: str, commit: str | None) -> dict | None:
    if not commit:
        return None

    def _inner() -> dict | None:
        try:
            from app.skills.core.git_service import git_service

            repo = git_service.repo
            commit_obj = repo.commit(commit)
            if not commit_obj.parents:
                return None
            summary = git_service.diff_summary(
                skill_id,
                commit_a=commit_obj.parents[0].hexsha,
                commit_b=commit_obj.hexsha,
            )
            for item in summary.get("files") or []:
                if isinstance(item, dict) and "path" in item:
                    item["path"] = _strip_skill_prefix(str(item["path"]), skill_id)
            return summary
        except Exception as exc:  # noqa: BLE001
            logger.debug("生成 AI 回合 diff summary 失败 skill={} commit={}: {}", skill_id, commit, exc)
            return None

    return await asyncio.to_thread(_inner)


def _serialize_message(message: SkillWorkbenchMessage, *, turn_id: str | None = None) -> dict:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "turn_id": turn_id,
        "role": message.role,
        "content": message.content,
        "intent": message.intent_json,
        "module_id": message.module_id,
        "created_at": isoformat_bjt(message.created_at),
    }


async def ensure_session_row(
    *,
    session_id: str,
    skill_id: str,
    user_id: str,
    mode: str = "edit",
) -> str:
    """确保 SkillWorkbenchSession 外键行存在。"""
    try:
        async with _sf()() as db:
            if not session_id or len(session_id) > 50:
                session_id = _hashed_session_id(f"{skill_id}:{user_id}:{session_id}")
            existing = await db.get(SkillWorkbenchSession, session_id)
            if existing is not None and (existing.skill_id != skill_id or existing.user_id != user_id):
                session_id = _hashed_session_id(f"{skill_id}:{user_id}:{session_id}")
                existing = await db.get(SkillWorkbenchSession, session_id)
            if existing is None:
                now = now_bjt()
                db.add(
                    SkillWorkbenchSession(
                        id=session_id,
                        skill_id=skill_id,
                        user_id=user_id,
                        mode=mode,
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                )
                await db.commit()
            return session_id
    except Exception as e:
        logger.warning("确保会话行失败 session={}: {}", session_id, e)
        return session_id


async def save_message(
    *,
    session_id: str,
    role: str,
    content: str,
    intent_json: dict | None = None,
    module_id: str | None = None,
    selection_range: dict | None = None,
    draft_revision: int | None = None,
) -> int | None:
    """持久化一条会话消息，返回消息 ID。失败返回 None（不阻断主流程）。"""
    try:
        async with _sf()() as db:
            msg = SkillWorkbenchMessage(
                session_id=session_id,
                role=role,
                content=content[:10000],  # 截断超长内容
                intent_json=intent_json,
                module_id=module_id,
                selection_range=selection_range,
                draft_revision=draft_revision,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg.id
    except Exception as e:
        logger.warning("会话消息持久化失败 session={}: {}", session_id, e)
        return None


async def load_history(session_id: str, limit: int = 50) -> list[dict]:
    """加载会话的历史消息（按时间正序）。"""
    try:
        async with _sf()() as db:
            stmt = (
                select(SkillWorkbenchMessage)
                .where(SkillWorkbenchMessage.session_id == session_id)
                .order_by(desc(SkillWorkbenchMessage.created_at))
                .limit(limit)
            )
            result = await db.execute(stmt)
            messages = result.scalars().all()
            return [
                _serialize_message(m)
                for m in reversed(messages)
            ]
    except Exception as e:
        logger.warning("加载会话历史失败 session={}: {}", session_id, e)
        return []


async def load_coding_timeline(skill_id: str, user_id: str, limit: int = 50) -> list[dict]:
    """按 skill/user 读取 coding timeline，合并该用户该 skill 下的所有 session。"""
    try:
        async with _sf()() as db:
            stmt = (
                select(SkillWorkbenchMessage)
                .join(SkillWorkbenchSession, SkillWorkbenchMessage.session_id == SkillWorkbenchSession.id)
                .where(SkillWorkbenchSession.skill_id == skill_id)
                .where(SkillWorkbenchSession.user_id == user_id)
                .order_by(desc(SkillWorkbenchMessage.created_at), desc(SkillWorkbenchMessage.id))
                .limit(limit)
            )
            result = await db.execute(stmt)
            messages = result.scalars().all()
            ordered = list(reversed(messages))
            current_turn_id: str | None = None
            serialized: list[dict] = []
            for m in ordered:
                if m.role == "user":
                    current_turn_id = f"turn-{m.id}"
                serialized.append(_serialize_message(m, turn_id=current_turn_id))
            return serialized
    except Exception as e:
        logger.warning("加载 coding timeline 失败 skill={} user={}: {}", skill_id, user_id, e)
        return []


async def load_recent_ai_turns(skill_id: str, user_id: str, limit: int = 10) -> list[dict]:
    """按 skill/user 返回最近 AI 修改回合，基于现有 workbench messages 聚合。"""
    try:
        async with _sf()() as db:
            stmt = (
                select(SkillWorkbenchMessage)
                .join(SkillWorkbenchSession, SkillWorkbenchMessage.session_id == SkillWorkbenchSession.id)
                .where(SkillWorkbenchSession.skill_id == skill_id)
                .where(SkillWorkbenchSession.user_id == user_id)
                .order_by(desc(SkillWorkbenchMessage.created_at), desc(SkillWorkbenchMessage.id))
                .limit(max(limit * 80, 200))
            )
            result = await db.execute(stmt)
            messages = list(reversed(result.scalars().all()))
    except Exception as e:
        logger.warning("加载 AI 修改回合失败 skill={} user={}: {}", skill_id, user_id, e)
        return []

    turns: list[dict] = []
    current: dict | None = None

    def finish_current() -> None:
        nonlocal current
        if current is not None:
            turns.append(current)
            current = None

    for m in messages:
        intent = m.intent_json if isinstance(m.intent_json, dict) else {}
        event_type = _event_type(m)
        if m.role == "user":
            finish_current()
            current = {
                "turn_id": f"turn-{m.id}",
                "session_id": m.session_id,
                "user_prompt": m.content,
                "changed_files": [],
                "git_commit": None,
                "git_commit_full": None,
                "diff_summary": None,
                "status": "running",
                "error": None,
                "error_reason": None,
                "quality": None,
                "ran": False,
                "ran_tests": False,
                "ran_samples": False,
                "used_real_data": False,
                "generated_reports": False,
                "generated_todos": False,
                "created_at": isoformat_bjt(m.created_at),
            }
            continue

        if current is None:
            continue

        if event_type == "file_change":
            _append_unique_file(
                current["changed_files"],
                str(intent.get("path") or intent.get("file_path") or m.content.replace("file_change:", "")),
                skill_id,
            )
        elif event_type == "git_commit":
            current["git_commit"] = intent.get("git_commit") or intent.get("commit")
            current["git_commit_full"] = intent.get("git_commit_full") or current["git_commit"]
            if isinstance(intent.get("diff_summary"), dict):
                current["diff_summary"] = intent["diff_summary"]
            if isinstance(intent.get("changed_files"), list):
                for path in intent["changed_files"]:
                    _append_unique_file(current["changed_files"], str(path), skill_id)
        elif event_type == "done":
            current["status"] = "success"
            current["ran"] = bool(intent.get("ran") or current.get("ran"))
            current["ran_tests"] = bool(intent.get("ran_tests") or current.get("ran_tests"))
            current["ran_samples"] = bool(intent.get("ran_samples") or current.get("ran_samples"))
            current["used_real_data"] = bool(intent.get("used_real_data") or current.get("used_real_data"))
            current["generated_reports"] = bool(intent.get("generated_reports") or current.get("generated_reports"))
            current["generated_todos"] = bool(intent.get("generated_todos") or current.get("generated_todos"))
            if intent.get("git_commit"):
                current["git_commit"] = intent.get("git_commit")
                current["git_commit_full"] = intent.get("git_commit_full") or current["git_commit"]
            if isinstance(intent.get("diff_summary"), dict):
                current["diff_summary"] = intent["diff_summary"]
            if isinstance(intent.get("changed_files"), list):
                for path in intent["changed_files"]:
                    _append_unique_file(current["changed_files"], str(path), skill_id)
        elif event_type == "error" or m.content == "error":
            current["status"] = "error"
            current["error"] = str(intent.get("error") or intent.get("message") or m.content or "AI 会话失败")
            current["error_reason"] = intent.get("error_reason") if isinstance(intent.get("error_reason"), dict) else attribute_ai_error(
                str(intent.get("code") or ""),
                current["error"],
            )
            current["ran"] = bool(intent.get("ran") or current.get("ran"))
            current["ran_tests"] = bool(intent.get("ran_tests") or current.get("ran_tests"))
            current["ran_samples"] = bool(intent.get("ran_samples") or current.get("ran_samples"))
            current["used_real_data"] = bool(intent.get("used_real_data") or current.get("used_real_data"))
            current["generated_reports"] = bool(intent.get("generated_reports") or current.get("generated_reports"))
            current["generated_todos"] = bool(intent.get("generated_todos") or current.get("generated_todos"))
        elif event_type == "tool_call" or event_type == "tool_result":
            text = str(intent.get("tool") or intent.get("content") or intent.get("result") or m.content).lower()
            if any(marker in text for marker in ("pytest", "python", "run", "执行", "测试")):
                current["ran"] = True
            if "pytest" in text or "test_main" in text:
                current["ran_tests"] = True
            if "sample_input" in text or "sandbox" in text or "fixtures/" in text:
                current["ran_samples"] = True
            if any(marker in text for marker in ("fetch_api", "capture_apis", "skillforge_sdk", "真实", "api")) and "sample_input" not in text:
                current["used_real_data"] = True
            if "reports" in text:
                current["generated_reports"] = True
            if "todos" in text:
                current["generated_todos"] = True
        elif event_type == "quality_summary" and isinstance(intent.get("quality"), dict):
            current["quality"] = intent["quality"]

    finish_current()

    recent = list(reversed(turns))[:limit]
    for turn in recent:
        if turn.get("git_commit_full") and not turn.get("diff_summary"):
            summary = await _diff_summary_for_commit(skill_id, str(turn["git_commit_full"]))
            if summary:
                turn["diff_summary"] = summary
                for item in summary.get("files") or []:
                    if isinstance(item, dict):
                        _append_unique_file(turn["changed_files"], str(item.get("path") or ""), skill_id)
        if turn["status"] == "running" and turn.get("git_commit"):
            turn["status"] = "success"
        if not turn.get("quality"):
            turn["quality"] = score_ai_turn_quality(turn)
    return recent
