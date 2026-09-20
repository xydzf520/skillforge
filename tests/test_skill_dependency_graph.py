"""Skill 依赖图路由测试。"""

import pytest

from app.datasources.models import DataSource
from app.skills.core.models import Skill


@pytest.mark.asyncio
async def test_skill_dependency_graph_lists_fork_datasource_and_playbook(client, monkeypatch):
    from app.database import async_session_factory

    async with async_session_factory() as session:
        session.add_all([
            Skill(
                id="skill-self",
                name="当前 Skill",
                description="self",
                department="AI小组",
                role="分析师",
                trigger_type="manual",
                risk_level="R2",
                owner="admin",
                status="active",
                forked_from="skill-parent",
            ),
            Skill(
                id="skill-parent",
                name="父 Skill",
                description="parent",
                department="AI小组",
                role="分析师",
                trigger_type="manual",
                risk_level="R2",
                owner="admin",
                status="active",
            ),
            Skill(
                id="skill-child",
                name="子 Skill",
                description="child",
                department="AI小组",
                role="分析师",
                trigger_type="manual",
                risk_level="R2",
                owner="admin",
                status="draft",
                forked_from="skill-self",
            ),
            DataSource(
                id="ds-graph",
                name="依赖数据源",
                department="AI小组",
                source_type="csv_upload",
                config={},
                is_active=True,
                visibility="company",
                owner_contact="admin",
                created_by="admin",
                related_skills=["skill-self"],
            ),
        ])
        await session.commit()

    async def fake_list_playbooks():
        return [{"file_name": "daily-quality", "name": "每日质检"}]

    async def fake_get_playbook(_name: str):
        return {
            "steps": [
                {"id": "step-1", "skill": "skill-self"},
                {"id": "step-2", "skill": "skill-other"},
            ]
        }

    monkeypatch.setattr("app.playbooks.service.list_playbooks", fake_list_playbooks)
    monkeypatch.setattr("app.playbooks.service.get_playbook", fake_get_playbook)

    resp = await client.get("/api/skills/skill-self/dependency-graph")
    assert resp.status_code == 200
    data = resp.json()

    node_ids = {node["id"] for node in data["nodes"]}
    assert {"skill-self", "skill-parent", "skill-child", "ds-graph", "playbook:daily-quality"} <= node_ids

    edge_set = {(edge["from"], edge["to"], edge["kind"]) for edge in data["edges"]}
    assert ("skill-parent", "skill-self", "forked-into") in edge_set
    assert ("skill-self", "skill-child", "forked-into") in edge_set
    assert ("skill-self", "ds-graph", "consumes") in edge_set
    assert ("playbook:daily-quality", "skill-self", "calls") in edge_set

    assert data["summary"] == {
        "skill_count": 3,
        "datasource_count": 1,
        "playbook_count": 1,
        "total_edges": 4,
    }
