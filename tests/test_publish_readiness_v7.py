import pytest


@pytest.mark.asyncio
async def test_task_contract_gate_check_from_file(monkeypatch):
    from app.skills import publish_readiness as pr

    def fake_read_file(skill_id, path):
        if path == "task-review-state.json":
            return '{"gate":{"can_publish":false,"items":[{"label":"人工确认","passed":false}]}}'
        if path == "task-contract.json":
            return '{"goal":"每天 18:00 发昨日销售钉钉日报"}'
        return None

    monkeypatch.setattr(pr.git_service, "read_file", fake_read_file)

    result = await pr._task_contract_gate_check("demo-skill")
    assert result["status"] == "blocked"
    assert result["gate"]["can_publish"] is False


def test_implementation_gate_blocks_placeholder_script(monkeypatch):
    from app.skills import publish_readiness as pr

    def fake_read_file(skill_id, path):
        if path == "scripts/main.py":
            return "import json\nprint(json.dumps({'conclusion': '待实现'}))\n"
        if path == "SKILL.md":
            return "## 输出定义\n- conclusion: 结论\n"
        return None

    monkeypatch.setattr(pr.git_service, "read_file", fake_read_file)

    result = pr._implementation_gate_check("demo-skill", "## 输出定义\n- conclusion: 结论\n")
    assert result["status"] == "blocked"
    assert any("占位" in e or "待实现" in e for e in result["errors"])


def test_implementation_gate_requires_sdk_for_external_url(monkeypatch):
    from app.skills import publish_readiness as pr

    def fake_read_file(skill_id, path):
        if path == "scripts/main.py":
            return "def main(payload):\n    return {'title': 'x'}\n"
        return None

    monkeypatch.setattr(pr.git_service, "read_file", fake_read_file)

    skill_md = "## 数据来源\n- Base URL: https://openapi.yuyidata.com/openapi/v3/\n"
    result = pr._implementation_gate_check("demo-skill", skill_md)
    assert result["status"] == "blocked"
    assert any("真实采集逻辑" in e for e in result["errors"])


@pytest.mark.asyncio
async def test_review_submission_gate_lists_required_missing_items(monkeypatch):
    from app.skills import publish_readiness as pr

    def fake_read_file(skill_id, path):
        if path == "scripts/main.py":
            return "def main(payload):\n    return {'title': 'x'}\n"
        if path == "contract.json":
            return "{}"
        if path == "SKILL.md":
            return "## 数据来源\n- https://example.com/api\n"
        return None

    class Result:
        def first(self):
            return None

    class DB:
        async def execute(self, stmt):
            return Result()

    monkeypatch.setattr(pr.git_service, "read_file", fake_read_file)
    monkeypatch.setattr(pr.git_service, "log", lambda skill_id, max_count=1: [])

    gate = await pr._review_submission_gate_check("demo-skill", DB(), "## 数据来源\n- https://example.com/api\n")

    assert gate["can_submit_review"] is False
    missing = {item["key"] for item in gate["items"] if not item["passed"]}
    assert {"git_commit", "test_or_sample_run", "output_schema", "runtime_data_acquisition"} <= missing
    assert gate["score"] < pr.REVIEW_GATE_BLOCK_THRESHOLD
