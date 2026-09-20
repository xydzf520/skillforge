"""Service facade for coach event enrichment and telemetry."""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession


def _load_structured_skill(skill_id: str):
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        logger.debug("coach: skill {} has no SKILL.md, falling back to empty structured document", skill_id)
        return skill_parser.parse("")
    return skill_parser.parse(skill_md)


async def run_coach_event(
    *,
    skill_id: str,
    event: str,
    context: dict | None,
    db: AsyncSession,
) -> dict:
    from app.workbench.coach import handle_event

    structured = _load_structured_skill(skill_id)
    enriched_context = dict(context or {})

    if event == "param_changed":
        param_name = enriched_context.get("param_name")
        if param_name:
            try:
                from app.skills.intelligence.param_index import find_param_usages

                cross = await find_param_usages(db, param_name, exclude_skill=skill_id)
                enriched_context["cross_skill_usages"] = cross
            except Exception as e:
                from loguru import logger
                logger.debug("coach: 跨 Skill 参数用法查询失败 param={}: {}", param_name, e)

    response = await handle_event(event, structured, context=enriched_context)

    if event == "reverted_repeatedly":
        try:
            from app.common.telemetry import record_repeated_edit

            location = enriched_context.get("location", "unknown")
            count = enriched_context.get("revert_count", 1)
            record_repeated_edit(location, int(count))
        except Exception as e:
            from loguru import logger
            logger.debug("coach: 重复编辑埋点失败: {}", e)

    return response.to_dict()


def record_coach_accepted(action: str | None) -> dict:
    from app.common.telemetry import record_coach_suggestion

    record_coach_suggestion(accepted=True, action=action or "unknown")
    return {"ok": True}
