"""Skill 依赖图组装逻辑。"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError
from app.datasources import hall_service as datasource_hall_service
from app.datasources.models import DataSource
from app.playbooks import service as playbook_service
from app.skills import hall_service as skill_hall_service
from app.skills.core.models import Skill
from app.skills.core.service_shared import ensure_skill_access


async def build_skill_dependency_graph(
    *,
    db: AsyncSession,
    skill_id: str,
    current_user: User,
) -> dict:
    """返回 Skill 的完整依赖图。"""
    await ensure_skill_access(db, skill_id, current_user, "read")

    self_skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not self_skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_node_ids: set[str] = set()

    def _append_node(node: dict) -> None:
        node_id = str(node["id"])
        if node_id in seen_node_ids:
            return
        seen_node_ids.add(node_id)
        nodes.append(node)

    _append_node(
        {
            "id": self_skill.id,
            "type": "skill",
            "label": self_skill.name,
            "status": self_skill.status,
            "department": self_skill.department,
            "is_self": True,
            "can_view": True,
        }
    )

    if self_skill.forked_from:
        parent_stmt = select(Skill).where(Skill.id == self_skill.forked_from)
        parent_stmt = await skill_hall_service._apply_visibility(db, current_user, parent_stmt)
        parent = (await db.execute(parent_stmt)).scalar_one_or_none()
        if parent:
            _append_node(
                {
                    "id": parent.id,
                    "type": "skill",
                    "label": parent.name,
                    "status": parent.status,
                    "department": parent.department,
                    "can_view": True,
                }
            )
            edges.append({"from": parent.id, "to": self_skill.id, "kind": "forked-into"})

    child_stmt = select(Skill).where(Skill.forked_from == self_skill.id).limit(20)
    child_stmt = await skill_hall_service._apply_visibility(db, current_user, child_stmt)
    children = (await db.execute(child_stmt)).scalars().all()
    for child in children:
        _append_node(
            {
                "id": child.id,
                "type": "skill",
                "label": child.name,
                "status": child.status,
                "department": child.department,
                "can_view": True,
            }
        )
        edges.append({"from": self_skill.id, "to": child.id, "kind": "forked-into"})

    use_pg_related_filter = skill_hall_service._is_postgresql(db)
    ds_stmt = select(DataSource).where(
        DataSource.is_active.is_(True),
        DataSource.related_skills.isnot(None),
    )
    if use_pg_related_filter:
        ds_stmt = ds_stmt.where(DataSource.related_skills.overlap([self_skill.id]))
    ds_stmt = await datasource_hall_service._apply_visibility(db, current_user, ds_stmt)
    ds_rows = (await db.execute(ds_stmt)).scalars().all()
    for data_source in ds_rows:
        if not use_pg_related_filter and self_skill.id not in (data_source.related_skills or []):
            continue
        _append_node(
            {
                "id": data_source.id,
                "type": "datasource",
                "label": data_source.name,
                "department": data_source.department,
                "can_view": True,
            }
        )
        edges.append({"from": self_skill.id, "to": data_source.id, "kind": "consumes"})

    matched_playbooks = 0
    for playbook in await playbook_service.list_playbooks():
        pb_name = str(playbook.get("name") or playbook.get("file_name") or "")
        pb_file = str(playbook.get("file_name") or pb_name)
        if not pb_file:
            continue
        try:
            full_pb = await playbook_service.get_playbook(pb_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dependency-graph 读取 playbook 失败 skill={} playbook={} err={}",
                skill_id,
                pb_file,
                exc,
            )
            continue
        steps = full_pb.get("steps") or []
        if not any(str(step.get("skill") or step.get("id") or "") == self_skill.id for step in steps):
            continue
        playbook_node_id = f"playbook:{pb_file}"
        _append_node(
            {
                "id": playbook_node_id,
                "route_id": pb_file,
                "type": "playbook",
                "label": pb_name or pb_file,
                "can_view": True,
            }
        )
        edges.append({"from": playbook_node_id, "to": self_skill.id, "kind": "calls"})
        matched_playbooks += 1
        if matched_playbooks >= 20:
            break

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "skill_count": sum(1 for node in nodes if node["type"] == "skill"),
            "datasource_count": sum(1 for node in nodes if node["type"] == "datasource"),
            "playbook_count": sum(1 for node in nodes if node["type"] == "playbook"),
            "total_edges": len(edges),
        },
    }
