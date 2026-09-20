import pytest
from sqlalchemy import select

from app.execution.models import DecisionLog
from app.inbox import report_designs


def test_open_design_report_template_catalog_counts_and_prompt_contract():
    catalog = report_designs.load_report_design_catalog()
    assert catalog["summary"] == {
        "design_system_count": 150,
        "skill_count": 154,
        "template_count": 304,
    }
    meta = report_designs.get_report_design_template("od-design-meta")
    assert meta["source_type"] == "design_system"
    assert meta["license"] == "Apache-2.0"
    assert "github.com/nexu-io/open-design" in meta["upstream"]
    assert "prompt" in meta["design_standard"]
    assert "报告" in meta["design_standard"]["prompt"]


def test_open_design_skill_templates_are_available_for_report_workflows():
    listing = report_designs.list_report_design_templates(source_type="skill", q="ui", page_size=200)
    assert listing["summary"]["skill_count"] == 154
    assert any(item["id"] == "od-skill-ui-skills" for item in listing["items"])
    skill_template = report_designs.get_report_design_template("od-skill-ui-skills")
    assert skill_template["source_type"] == "skill"
    assert "先结论" in skill_template["design_standard"]["prompt"] or "报告" in skill_template["design_standard"]["prompt"]


@pytest.mark.asyncio
async def test_report_design_template_api_lists_open_design_catalog(client):
    resp = await client.get("/api/inbox/report-design-templates", params={"page_size": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["design_system_count"] == 150
    assert body["summary"]["skill_count"] == 154
    assert body["total"] == 304
    assert len(body["items"]) == 2

    detail = await client.get("/api/inbox/report-design-templates/od-design-meta")
    assert detail.status_code == 200, detail.text
    assert detail.json()["design_standard"]["prompt"]


@pytest.mark.asyncio
async def test_project_report_generation_applies_selected_open_design_template(client):
    await client.post(
        "/api/projects/",
        json={
            "id": "report_design_page",
            "name": "可选报告模板页面",
            "type": "external_web",
            "entry_url": "https://example.com/report-design",
            "metadata": {"report_design_template_id": "od-design-meta"},
        },
    )
    run = (
        await client.post(
            "/api/projects/report_design_page/runs",
            json={
                "request_id": "design-open-001",
                "input": {"region": "华南", "report_design_template_id": "od-design-meta"},
            },
        )
    ).json()
    result = (
        await client.post(
            f"/api/projects/runs/{run['id']}/ingest",
            json={
                "request_id": "design-output-001",
                "auto_analyze": False,
                "output": {"summary": "按模板输出报告"},
                "reports": [
                    {
                        "title": "模板化运营报告",
                        "summary": "按 Meta 设计标准生成首屏结论和指标。",
                        "payload": {"store_overview": {"item_count": 3}},
                    }
                ],
            },
        )
    ).json()

    report = result["output_result"]["reports"][0]
    design = report["payload"]["_report_design"]
    assert design["template_id"] == "od-design-meta"
    assert design["source_type"] == "design_system"
    assert design["license"] == "Apache-2.0"
    assert design["design_standard"]["prompt"]
    assert "设计模板" in report["tags"]
    assert result["output_result"]["_skillforge_meta"]["report_design"]["template_id"] == "od-design-meta"

    import app.database as db_mod

    async with db_mod.async_session_factory() as db:
        decision = (
            await db.execute(select(DecisionLog).where(DecisionLog.run_id == run["execution_run_id"]))
        ).scalar_one()
    stored_report = decision.output_result["reports"][0]
    assert stored_report["payload"]["_report_design"]["template_id"] == "od-design-meta"
