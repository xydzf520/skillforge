import json
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_sf_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "sf.py"
    spec = importlib.util.spec_from_file_location("sf_cli_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_mcp_catalog_summary_is_readable_and_keeps_json_escape_hatch():
    sf = _load_sf_module()
    summary = sf.format_mcp_catalog_summary({
        "servers": [{
            "name": "skillforge",
            "tools": [
                {
                    "name": "skillforge_org_search_users",
                    "description": "查询当前账号可见范围内的组织成员。",
                    "meta": {"platform": "skillforge", "write": False, "requires_shop_id": False},
                },
                {
                    "name": "skillforge_dingtalk_send_work_notice",
                    "description": "向组织成员发送钉钉工作通知。",
                    "meta": {"platform": "skillforge", "write": True, "requires_shop_id": False},
                },
                {
                    "name": "skillforge_agent_coverage",
                    "description": "查询部门 Agent 覆盖度。",
                    "meta": {"platform": "skillforge", "write": False, "requires_shop_id": False},
                },
                {
                    "name": "skillforge_ai_analyze",
                    "description": "调用平台 AI。",
                    "meta": {"platform": "skillforge", "write": False, "requires_shop_id": False},
                },
                {
                    "name": "skillforge_raw_data_query",
                    "description": "查询 Skill 运行后的原始数据。",
                    "meta": {"platform": "skillforge", "write": False, "requires_shop_id": False},
                },
                {
                    "name": "skillforge_run_analyze",
                    "description": "一键复盘 Skill 运行。",
                    "meta": {"platform": "skillforge", "write": False, "requires_shop_id": False},
                },
                {
                    "name": "tmall_item_reviews",
                    "description": "采集商品评价。",
                    "meta": {"platform": "tmall", "write": False, "requires_shop_id": True},
                },
            ],
        }],
    })

    assert "SkillForge MCP 可用工具" in summary
    assert "工具: 7 | 只读: 6 | 写入: 1" in summary
    assert "skillforge_agent_coverage [skillforge]" in summary
    assert "skillforge_ai_analyze [skillforge]" in summary
    assert "skillforge_raw_data_query [skillforge]" in summary
    assert "skillforge_run_analyze [skillforge]" in summary
    assert "skillforge_org_search_users [skillforge]" in summary
    assert "tmall_item_reviews [shop tmall]" in summary
    assert "skillforge_dingtalk_send_work_notice [write skillforge]" in summary
    assert "sf mcp catalog --json" in summary


def test_mcp_catalog_summary_surfaces_client_known_builtin_skew():
    sf = _load_sf_module()
    summary = sf.format_mcp_catalog_summary({
        "servers": [{
            "name": "skillforge",
            "tools": [
                {
                    "name": "skillforge_org_search_users",
                    "description": "查询当前账号可见范围内的组织成员。",
                    "meta": {"platform": "skillforge", "write": False, "requires_shop_id": False},
                },
                {
                    "name": "skillforge_dingtalk_send_work_notice",
                    "description": "向组织成员发送钉钉工作通知。",
                    "meta": {"platform": "skillforge", "write": True, "requires_shop_id": False},
                },
            ],
        }],
    })

    assert "工具: 2 | 只读: 1 | 写入: 1" in summary
    assert "本地 CLI 已知但当前平台 catalog 未返回" in summary
    assert "skillforge_agent_coverage [skillforge]" in summary
    assert "skillforge_ai_analyze [skillforge]" in summary
    assert "skillforge_raw_data_query [skillforge]" in summary
    assert "skillforge_run_analyze [skillforge]" in summary
    assert "版本不一致" in summary


def test_api_request_non_json_response_exits_without_traceback(monkeypatch):
    sf = _load_sf_module()

    class DummyResponse:
        status_code = 200
        text = "<html>not found</html>"
        url = "http://skillforge.test/api/aiclaw/departments/agent-coverage"
        headers = {"content-type": "text/html"}

        def json(self):
            raise ValueError("not json")

    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/api/aiclaw/departments/agent-coverage")
        return DummyResponse()

    monkeypatch.setattr(sf.requests, "request", fake_request)
    monkeypatch.setattr(sf, "base_url", lambda: "http://skillforge.test")

    with pytest.raises(SystemExit) as exc:
        sf.api_request("GET", "/api/aiclaw/departments/agent-coverage", auth=False)

    message = str(exc.value)
    assert "响应不是 JSON" in message
    assert "200 /api/aiclaw/departments/agent-coverage" in message
    assert "text/html" in message
    assert "not found" in message


def test_ai_analyze_cli_calls_platform_ai_mcp(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "data": {"output": "ok"}}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.ai_analyze(SimpleNamespace(
        prompt="分析这次运行",
        system="",
        context="",
        context_json='{"run_id":"run-1"}',
        skill_id="skill-1",
        json_mode=True,
        max_output_tokens=1024,
        temperature=0.1,
    ))

    assert json.loads(capsys.readouterr().out)["data"]["output"] == "ok"
    method, path, kwargs = calls[0]
    body = kwargs["json_body"]
    assert method == "POST"
    assert path == "/api/codex/mcp/call"
    assert body["tool"] == "skillforge_ai_analyze"
    assert body["run_mode"] == "sf_ai_analyze"
    assert body["arguments"]["context_pack"] == {"run_id": "run-1"}
    assert body["arguments"]["json_mode"] is True


def test_agent_coverage_cli_calls_coverage_endpoint(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "data": {
            "items": [{
                "department": "EC",
                "status": "fallback",
                "missing": [],
                "fallback": ["analysis", "training"],
                "capabilities": {
                    "skill_runtime": {"ready": True, "online": 1, "count": 1},
                    "analysis": {"ready": False, "fallback_ready": True, "fallback_count": 1},
                    "training": {"ready": False, "fallback_ready": True, "fallback_count": 1},
                },
            }],
            "total": 1,
            "summary": {"ready": 0, "fallback": 1, "missing": 0},
        }}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.agent_coverage(SimpleNamespace(department="", status="", json=False))

    output = capsys.readouterr().out
    assert "SkillForge Agent 覆盖度" in output
    assert "EC [平台兜底]" in output
    assert "执行 1/1" in output
    assert "分析 兜底 1" in output
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/codex/mcp/call"
    body = calls[0][2]["json_body"]
    assert body["tool"] == "skillforge_agent_coverage"
    assert body["run_mode"] == "sf_agent_coverage"
    assert body["dry_run"] is True


def test_agent_analysis_cli_calls_codex_analysis_agent_endpoint(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "agent": {"id": "samplebrand-same-topic-video-diagnosis-agent"}}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.agent_analysis_create(SimpleNamespace(
        agent_id="samplebrand-same-topic-video-diagnosis-agent",
        name="示例品牌同主题视频消耗诊断 Agent",
        department="示例品牌内容电商运营部",
        department_id="",
        owner="祁莹莹",
        owner_user_id="",
        skill_id="samplebrand-weekly-video-diagnosis",
        prompt_version="analysis_v1",
        description="默认周度诊断",
        dimension=["首屏钩子", "商品露出"],
        dimensions="",
        default_params='{"top_n":5}',
        editor=["祁莹莹"],
        editor_user_id=[],
        status="active",
    ))

    assert json.loads(capsys.readouterr().out)["agent"]["id"] == "samplebrand-same-topic-video-diagnosis-agent"
    method, path, kwargs = calls[0]
    body = kwargs["json_body"]
    assert method == "POST"
    assert path == "/api/codex/agents/analysis"
    assert body["owner_query"] == "祁莹莹"
    assert body["skill_id"] == "samplebrand-weekly-video-diagnosis"
    assert body["dimensions"] == ["首屏钩子", "商品露出"]
    assert body["default_params"]["top_n"] == 5


def test_agent_analysis_list_prints_control_summary(monkeypatch, capsys):
    sf = _load_sf_module()

    def fake_api_request(method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/codex/agents/analysis?department=%E6%9D%9C%E8%95%BE%E6%96%AF%E5%86%85%E5%AE%B9%E7%94%B5%E5%95%86%E8%BF%90%E8%90%A5%E9%83%A8"
        return {
            "items": [{
                "name": "示例品牌同主题视频消耗诊断 Agent",
                "department": "示例品牌内容电商运营部",
                "skill_id": "samplebrand-weekly-video-diagnosis",
                "prompt_version": "analysis_v2",
                "control": {
                    "effective": {
                        "default_params": {
                            "preset": "brief_summary",
                            "window_days": 14,
                            "top_n": 2,
                            "output_sections": ["executive_summary", "topic_comparisons"],
                            "todo_enabled": False,
                        },
                    },
                },
            }],
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.agent_analysis_list(SimpleNamespace(department="示例品牌内容电商运营部", json=False))
    out = capsys.readouterr().out
    assert "samplebrand-weekly-video-diagnosis" in out
    assert "prompt=analysis_v2" in out
    assert "preset=brief_summary" in out
    assert "window_days=14" in out
    assert "top_n=2" in out
    assert "sections=executive_summary,topic_comparisons" in out
    assert "todos=off" in out


def test_agent_analysis_validate_calls_codex_validate_endpoint(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "ok": True,
            "checks": [
                {"key": "bound_skill", "label": "绑定 Skill", "status": "passed", "detail": "samplebrand-weekly-video-diagnosis"},
                {"key": "traceability", "label": "追溯字段", "status": "passed"},
            ],
            "sample_payload": {
                "skill_id": "samplebrand-weekly-video-diagnosis",
                "params": {"_analysis_agent_id": "samplebrand-same-topic-video-diagnosis-agent"},
            },
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.agent_analysis_validate(SimpleNamespace(
        id="samplebrand-same-topic-video-diagnosis-agent",
        params='{"top_n":2}',
        json=False,
    ))

    out = capsys.readouterr().out
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/codex/agents/analysis/samplebrand-same-topic-video-diagnosis-agent/validate"
    assert kwargs["json_body"]["params"]["top_n"] == 2
    assert "业务分析 Agent 验证: 通过" in out
    assert "sample agent: samplebrand-same-topic-video-diagnosis-agent" in out


def test_cloud_video_weekly_diagnosis_template_contains_governed_mcp_and_prompt(tmp_path):
    sf = _load_sf_module()
    files = sf._skill_template_files(
        "samplebrand-weekly-video-diagnosis",
        name="示例品牌周度同主题视频消耗诊断",
        description="",
        department="示例品牌内容电商运营部",
        trigger_type="cron",
        cron="0 8 * * *",
        risk_level="R2",
        template="cloud-video-weekly-diagnosis",
    )

    assert "prompts/analysis_v1.md" in files
    assert "skillforge_cloud_video_daily_person_video_report" in files["scripts/main.py"]
    assert "skillforge_cloud_video_video_usage_report" in files["scripts/main.py"]
    assert "skillforge_cloud_video_audit_rejects" in files["scripts/main.py"]
    assert "skillforge_dingtalk_send_work_notice" not in files["scripts/main.py"]
    assert "reports\": []" in files["scripts/main.py"]
    assert "todos\": []" in files["scripts/main.py"]
    assert "raw_data" in files["scripts/main.py"]
    assert "VIDEO_BATCH_MAX_PAGES = 20" in files["scripts/main.py"]
    assert "fetch_warnings" in files["scripts/main.py"]
    assert "\"auto_page\": True" in files["scripts/main.py"]
    assert "truncated at" in files["scripts/main.py"]
    assert "analysis_disabled" in files["scripts/main.py"]
    assert "todo_disabled" in files["scripts/main.py"]
    assert "notification_disabled" in files["scripts/main.py"]
    assert "不做 AI 诊断、不生成待办、不推送钉钉" in files["SKILL.md"]
    assert "示例品牌内容电商运营部" in files["SKILL.md"]
    assert "0 8 * * *" in files["SKILL.md"]
    contract = json.loads(files["contract.json"])
    assert contract["output_schema"]["required"] == ["collection_schema", "reports", "todos", "raw_data", "_skillforge_meta"]
    report_item = contract["output_schema"]["properties"]["reports"]["items"]
    todo_item = contract["output_schema"]["properties"]["todos"]["items"]
    raw_data = contract["output_schema"]["properties"]["raw_data"]
    assert report_item["required"] == ["channel", "title", "summary", "recipients"]
    assert todo_item["required"] == ["kind", "title"]
    assert raw_data["required"] == [
        "collection_schema",
        "date_range",
        "videos",
        "audit_videos",
        "daily_report",
        "usage_team",
        "usage_people",
        "audit_rejects",
    ]


def test_cloud_video_weekly_diagnosis_template_agent_control_changes_output(tmp_path):
    sf = _load_sf_module()
    files = sf._skill_template_files(
        "samplebrand-weekly-video-diagnosis",
        name="示例品牌周度同主题视频消耗诊断",
        description="",
        department="示例品牌内容电商运营部",
        trigger_type="cron",
        cron="0 8 * * *",
        risk_level="R2",
        template="cloud-video-weekly-diagnosis",
    )
    root = tmp_path / "samplebrand-weekly-video-diagnosis"
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    sdk_src = Path(__file__).resolve().parents[1] / "app" / "skill_runtime_sdk" / "skillforge_sdk.py"
    shutil.copyfile(sdk_src, root / "scripts" / "skillforge_sdk.py")

    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "main.py")],
        input=json.dumps({
            "sample_data": True,
            "dry_run": True,
            "videos": [],
            "analysis_agent": {
                "id": "samplebrand-same-topic-video-diagnosis-agent",
                "name": "示例品牌同主题视频消耗诊断 Agent",
                "prompt_version": "analysis_v2",
                "dimensions": ["商品露出", "行动引导"],
                "default_params": {
                    "preset": "brief_summary",
                    "top_n": 2,
                    "window_days": 14,
                    "output_sections": ["executive_summary", "topic_comparisons"],
                    "todo_enabled": False,
                },
            },
        }, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    assert data["reports"] == []
    assert data["todos"] == []
    assert data["raw_data"]["collection_schema"] == "samplebrand_cloud_video_weekly_collection_v1"
    assert data["raw_data"]["videos"] == []
    assert data["_skillforge_meta"]["collector"] is True
    assert data["_skillforge_meta"]["analysis_disabled"] is True
    assert data["_skillforge_meta"]["todo_disabled"] is True
    assert data["_skillforge_meta"]["notification_disabled"] is True
    assert data["_skillforge_meta"]["sample_used"] is True


def test_run_skill_script_injects_skill_commit_for_real_mcp(monkeypatch, tmp_path):
    sf = _load_sf_module()
    root = tmp_path / "skill"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "main.py").write_text("print('{}')\n", encoding="utf-8")
    captured = {}

    class FakeCompletedProcess:
        returncode = 0
        stdout = "{}\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update({"cmd": cmd, **kwargs})
        return FakeCompletedProcess()

    monkeypatch.setattr(sf, "runtime_sdk_path", lambda: tmp_path / "sdk")
    monkeypatch.setattr(sf, "base_url", lambda: "http://skillforge.test")
    monkeypatch.setattr(sf.subprocess, "run", fake_run)

    code, stdout, stderr = sf.run_skill_script(
        root,
        {
            "skill_id": "samplebrand-weekly-video-diagnosis",
            "run_id": "run-1",
            "run_mode": "local_debug",
            "run_token": "token-1",
            "runtime_run_token": "analysis-token-1",
            "base_commit": "c" * 40,
        },
        real_mcp=True,
        input_json={"dry_run": False},
    )

    assert code == 0
    assert stdout == "{}\n"
    assert stderr == ""
    env = captured["env"]
    assert env["SKILLFORGE_SKILL_ROOT"] == str(root)
    assert env["SKILLFORGE_SKILL_GIT_COMMIT_FULL"] == "c" * 40
    assert env["SKILLFORGE_RUN_TOKEN"] == "token-1"
    assert env["SKILLFORGE_INTELLIGENCE_RUN_TOKEN"] == "analysis-token-1"
    assert env["SKILLFORGE_MCP_GATEWAY_URL"] == "http://skillforge.test/api/codex/mcp/call"


def test_raw_query_cli_calls_raw_data_mcp(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "data": {"count": 1}}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.raw_query(SimpleNamespace(
        source="decision_logs",
        skill_id="skill-1",
        run_id="run-1",
        proof_id="",
        platform="",
        shop_id="",
        data_scope="",
        status="",
        limit=20,
        include_payload=True,
        include_endpoint=False,
    ))

    assert json.loads(capsys.readouterr().out)["data"]["count"] == 1
    method, path, kwargs = calls[0]
    body = kwargs["json_body"]
    assert method == "POST"
    assert path == "/api/codex/mcp/call"
    assert body["tool"] == "skillforge_raw_data_query"
    assert body["run_mode"] == "sf_raw_query"
    assert body["arguments"]["source"] == "decision_logs"
    assert body["arguments"]["skill_id"] == "skill-1"


def test_run_analyze_cli_calls_run_analysis_mcp(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "data": {"analysis": "ok"}}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.run_analyze(SimpleNamespace(
        run_id="run-1",
        skill_id="skill-1",
        prompt="复盘",
        include_raw=True,
        json_mode=False,
        max_output_tokens=2048,
        temperature=0.2,
        step_limit=10,
        decision_limit=20,
        proof_limit=30,
        snapshot_limit=5,
    ))

    assert json.loads(capsys.readouterr().out)["data"]["analysis"] == "ok"
    method, path, kwargs = calls[0]
    body = kwargs["json_body"]
    assert method == "POST"
    assert path == "/api/codex/mcp/call"
    assert body["tool"] == "skillforge_run_analyze"
    assert body["run_mode"] == "sf_run_analyze"
    assert body["arguments"]["run_id"] == "run-1"
    assert body["arguments"]["include_raw"] is True
    assert body["arguments"]["step_limit"] == 10


def test_project_submit_cli_uses_codex_project_upload_and_tar_hash(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "project"
    (root / "web").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: cli_project\nname: CLI 项目\nkind: web_static\nentry: web/index.html\n",
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        package = kwargs["files"]["package"][1]
        assert kwargs["data"]["package_hash"] == "sha256:" + sf.hashlib.sha256(package).hexdigest()
        return {"ok": True, "project_id": "cli_project"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "cli_project"
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/codex/projects/upload"
    assert json.loads(kwargs["data"]["manifest_json"])["project_id"] == "cli_project"


def test_project_submit_accepts_manifest_yaml_alias(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "manifest-alias-project"
    (root / "web").mkdir(parents=True)
    (root / "manifest.yaml").write_text(
        "project_id: alias_project\nname: Manifest Alias 项目\nkind: web_static\nentry: web/index.html\n",
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text("<html>alias</html>", encoding="utf-8")
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        package = kwargs["files"]["package"][1]
        with sf.tarfile.open(fileobj=sf.io.BytesIO(package), mode="r:gz") as tar:
            assert "manifest.yaml" in tar.getnames()
        return {"ok": True, "project_id": "alias_project"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "alias_project"
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/codex/projects/upload"
    assert json.loads(kwargs["data"]["manifest_json"])["project_id"] == "alias_project"


def test_project_submit_accepts_projectforge_json_alias(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "projectforge-json-project"
    (root / "web").mkdir(parents=True)
    (root / "projectforge.json").write_text(
        json.dumps({"project_id": "json_project", "name": "JSON 项目", "kind": "web_static", "entry": "web/index.html"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text("<html>json</html>", encoding="utf-8")
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        package = kwargs["files"]["package"][1]
        with sf.tarfile.open(fileobj=sf.io.BytesIO(package), mode="r:gz") as tar:
            assert "projectforge.json" in tar.getnames()
        return {"ok": True, "project_id": "json_project"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "json_project"
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/codex/projects/upload"
    assert json.loads(kwargs["data"]["manifest_json"])["project_id"] == "json_project"


def test_project_submit_allows_provider_endpoint_to_reach_codex_upload(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "provider-endpoint-submit"
    (root / "web").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: provider_endpoint_submit\n"
        "name: Provider Endpoint Submit\n"
        "kind: web_static\n"
        "entry: web/index.html\n"
        "capabilities:\n"
        "  - ai.cheap.generate\n",
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text(
        "<html><script>fetch('https://api.deepseek.com/v1/chat/completions',{method:'POST',body:'{}'})</script></html>",
        encoding="utf-8",
    )
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        package = kwargs["files"]["package"][1]
        with sf.tarfile.open(fileobj=sf.io.BytesIO(package), mode="r:gz") as tar:
            html = tar.extractfile("web/index.html").read().decode("utf-8")
        assert "https://api.deepseek.com/v1/chat/completions" in html
        assert "sk-" not in html
        return {"ok": True, "project_id": "provider_endpoint_submit"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "provider_endpoint_submit"
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/codex/projects/upload"
    assert json.loads(kwargs["data"]["manifest_json"])["capabilities"] == ["ai.cheap.generate"]


def test_project_init_external_url_manifest_uses_auto_kind(tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "external-project"

    sf.project_init(SimpleNamespace(
        path=str(root),
        project_id="external_cli_project",
        name="外部 CLI 项目",
        kind="",
        entry="web/index.html",
        entry_url="https://example.com/app",
        capability=["ai.generate"],
        force=False,
    ))

    payload = json.loads(capsys.readouterr().out)
    manifest = sf.yaml.safe_load((root / "projectforge.yaml").read_text(encoding="utf-8"))
    assert payload["kind"] == "external_web"
    assert payload["entry"] == "https://example.com/app"
    assert manifest["kind"] == "external_web"
    assert manifest["entry_url"] == "https://example.com/app"
    assert manifest["capabilities"] == ["ai.generate"]
    assert not (root / "web" / "index.html").exists()


def test_project_init_rejects_unsafe_external_entry_url(tmp_path):
    sf = _load_sf_module()
    root = tmp_path / "unsafe-external-project"

    with pytest.raises(SystemExit) as exc:
        sf.project_init(SimpleNamespace(
            path=str(root),
            project_id="unsafe_external_cli",
            name="不安全外部项目",
            kind="external_web",
            entry="web/index.html",
            entry_url="javascript:alert(1)",
            capability=["ai.generate"],
            force=False,
        ))

    assert "http/https" in str(exc.value)


def test_project_init_static_template_records_input_before_ai_and_output(tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "static-project"

    sf.project_init(SimpleNamespace(
        path=str(root),
        project_id="static_cli_project",
        name="静态 CLI 项目",
        kind="",
        entry="web/index.html",
        entry_url="",
        capability=[],
        force=False,
    ))

    payload = json.loads(capsys.readouterr().out)
    manifest = sf.yaml.safe_load((root / "projectforge.yaml").read_text(encoding="utf-8"))
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    assert payload["kind"] == "web_static"
    assert manifest["capabilities"] == ["ai.generate", "ai.analyze"]
    assert "gateway.input" in html
    assert html.count("const recorded = await gateway.input") == 1
    assert "recordCurrentInput" in html
    assert "sf_project_template" in html
    assert "gateway.capability" in html
    assert "gateway.ingest" in html


def test_project_spec_short_video_recipe_exposes_codex_contract():
    sf = _load_sf_module()

    spec = sf.project_generation_spec("short-video-analysis")

    assert spec["project_contract"]["visibility"] == ["company", "department", "private"]
    assert "uploadAsset" in spec["gateway_sdk"]["methods"]
    assert spec["visual_analysis"]["video_metadata"]["role"] == "material_a|material_b"
    assert spec["recipe"]["default_project_id"] == "short-video-analysis-mvp"
    assert spec["recipe"]["default_department"] == "示例品牌内容电商运营部"


def test_project_doctor_short_video_recipe_checks_generated_contract(tmp_path):
    sf = _load_sf_module()
    root = tmp_path / "short-video"
    (root / "web").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: short-video-analysis-mvp\n"
        "name: 短视频投放素材分析 MVP\n"
        "kind: web_static\n"
        "visibility: company\n"
        "entry: web/index.html\n"
        "capabilities:\n"
        "  - ai.generate\n"
        "  - ai.analyze\n"
        "outputs:\n"
        "  reports: true\n"
        "  todos: true\n"
        "  proofs: true\n",
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text(
        """
        <script src="/project-gateway-sdk.js"></script>
        <script>
        const gateway = window.PlatformProjectGateway;
        async function run(fileA, fileB) {
          await gateway.input({ input: { material_a: fileA.name, material_b: fileB.name } });
          await gateway.uploadAsset({ file: fileA, metadata: { role: 'material_a', material_role: 'material_a', asset_type: 'original_video' } });
          await gateway.uploadAsset({ file: fileB, metadata: { role: 'material_b', material_role: 'material_b', asset_type: 'original_video' } });
          const canvas = document.createElement('canvas');
          await gateway.uploadAsset({ file: new Blob(), metadata: { role: 'material_b', asset_type: 'visual_frame', frame_time: 1, frame_label: 'opening', source_file: fileB.name } });
          await gateway.analyze({ request_id: 'analyze-materials' });
          await gateway.ingest({ reports: [], proofs: [] });
        }
        </script>
        """,
        encoding="utf-8",
    )

    result = sf.project_doctor(SimpleNamespace(path=str(root), quiet=True, recipe="short-video-analysis"))

    assert result["ok"] is True
    assert result["recipe"]["ok"] is True
    assert {item["id"] for item in result["recipe"]["checks"]} >= {
        "capability.ai_analyze",
        "visual.material_roles",
        "visual.frame_fallback",
    }


def test_project_doctor_short_video_recipe_reports_missing_frame_fallback(tmp_path):
    sf = _load_sf_module()
    root = tmp_path / "short-video-missing-fallback"
    (root / "web").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: short-video-analysis-mvp\n"
        "name: 短视频投放素材分析 MVP\n"
        "kind: web_static\n"
        "entry: web/index.html\n"
        "capabilities:\n"
        "  - ai.generate\n"
        "  - ai.analyze\n"
        "outputs:\n"
        "  reports: true\n"
        "  proofs: true\n",
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text(
        """
        <script src="/project-gateway-sdk.js"></script>
        <script>
        const gateway = window.PlatformProjectGateway;
        gateway.input({ input: { material_a: 'a.mp4', material_b: 'b.mp4' } });
        gateway.uploadAsset({ file: new Blob(), metadata: { role: 'material_a' } });
        gateway.uploadAsset({ file: new Blob(), metadata: { role: 'material_b' } });
        gateway.analyze({});
        </script>
        """,
        encoding="utf-8",
    )

    result = sf.project_doctor(SimpleNamespace(path=str(root), quiet=True, recipe="short-video-analysis"))

    failed = {item["id"] for item in result["recipe"]["checks"] if not item["ok"]}
    assert result["ok"] is False
    assert "visual.frame_fallback" in failed


def test_project_doctor_rejects_frontend_ai_secret(tmp_path):
    sf = _load_sf_module()
    root = tmp_path / "unsafe-project"
    (root / "web").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: unsafe_cli_project\nname: Unsafe CLI 项目\nkind: web_static\nentry: web/index.html\n",
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text(
        "<html><script>fetch('https://api.openai.com/v1/chat/completions',{headers:{Authorization:'Bearer sk-testunsafeprojectsecret000000000000'}})</script></html>",  # public-scan: synthetic-fixture; deliberate redaction/rejection test
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        sf.project_doctor(SimpleNamespace(path=str(root), quiet=True))

    message = str(exc.value)
    assert "Project Gateway" in message
    assert "web/index.html" in message


def test_project_doctor_allows_provider_endpoint_as_gateway_proxy_candidate(tmp_path):
    sf = _load_sf_module()
    root = tmp_path / "provider-endpoint-project"
    (root / "web").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: provider_endpoint_cli_project\n"
        "name: Provider Endpoint CLI 项目\n"
        "kind: web_static\n"
        "entry: web/index.html\n"
        "capabilities:\n"
        "  - ai.generate\n",
        encoding="utf-8",
    )
    (root / "web" / "index.html").write_text(
        "<html><script>fetch('https://api.openai.com/v1/chat/completions',{method:'POST',body:'{}'})</script></html>",
        encoding="utf-8",
    )

    result = sf.project_doctor(SimpleNamespace(path=str(root), quiet=True))

    assert result["ok"] is True
    assert result["static_security"]["frontend_secret_scan"] == "pass"
    assert result["static_security"]["direct_ai_endpoint_count"] == 1
    assert result["static_security"]["direct_ai_endpoints"][0]["match"] == "openai_endpoint"
    assert result["static_security"]["ai_endpoint_policy"] == "allowed_with_project_gateway_proxy"
    assert result["ai_takeover"]["status"] == "gateway_proxy_candidate"


def test_project_doctor_rejects_unbuilt_frontend_source_entry(tmp_path):
    sf = _load_sf_module()
    root = tmp_path / "unbuilt-vite"
    (root / "src").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: unbuilt_vite\nname: Unbuilt Vite\nkind: web_static\nentry: index.html\n",
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        '<html><body><div id="app"></div><script type="module" src="/src/main.tsx"></script></body></html>',
        encoding="utf-8",
    )
    (root / "src" / "main.tsx").write_text("console.log('needs build')", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        sf.project_doctor(SimpleNamespace(path=str(root), quiet=True))

    message = str(exc.value)
    assert "dist/build/out" in message
    assert "index.html -> /src/main.tsx" in message


def test_project_init_auto_detects_existing_dist_index(tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "vite-project"
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "index.html").write_text("<html>vite</html>", encoding="utf-8")

    sf.project_init(SimpleNamespace(
        path=str(root),
        project_id="vite_cli_project",
        name="Vite CLI 项目",
        kind="",
        entry="",
        entry_url="",
        capability=[],
        force=False,
    ))

    payload = json.loads(capsys.readouterr().out)
    manifest = sf.yaml.safe_load((root / "projectforge.yaml").read_text(encoding="utf-8"))
    assert payload["entry"] == str(root / "dist" / "index.html")
    assert manifest["entry"] == "dist/index.html"
    assert not (root / "web" / "index.html").exists()


def test_project_init_auto_detects_existing_build_index(tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "react-project"
    (root / "build").mkdir(parents=True)
    (root / "build" / "index.html").write_text("<html>react</html>", encoding="utf-8")

    sf.project_init(SimpleNamespace(
        path=str(root),
        project_id="react_cli_project",
        name="React CLI 项目",
        kind="",
        entry="",
        entry_url="",
        capability=[],
        force=False,
    ))

    payload = json.loads(capsys.readouterr().out)
    manifest = sf.yaml.safe_load((root / "projectforge.yaml").read_text(encoding="utf-8"))
    assert payload["entry"] == str(root / "build" / "index.html")
    assert manifest["entry"] == "build/index.html"
    assert not (root / "web" / "index.html").exists()


def test_project_submit_packages_out_static_export(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "next-export"
    (root / "out").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: next_export\nname: Next Export\nkind: web_static\nentry: out/index.html\n",
        encoding="utf-8",
    )
    (root / "out" / "index.html").write_text("<html>next</html>", encoding="utf-8")
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        package = kwargs["files"]["package"][1]
        with sf.tarfile.open(fileobj=sf.io.BytesIO(package), mode="r:gz") as tar:
            names = tar.getnames()
        assert "out/index.html" in names
        return {"ok": True, "project_id": "next_export"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "next_export"
    assert calls[0][1] == "/api/codex/projects/upload"


def test_project_submit_packages_root_generated_static_dirs(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "root-generated"
    (root / "static" / "js").mkdir(parents=True)
    (root / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: root_generated\nname: Root Generated\nkind: web_static\nentry: index.html\n",
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        '<html><script src="/static/js/main.js"></script><script src="/_next/static/chunks/app.js"></script></html>',
        encoding="utf-8",
    )
    (root / "static" / "js" / "main.js").write_text("console.log('cra')", encoding="utf-8")
    (root / "_next" / "static" / "chunks" / "app.js").write_text("console.log('next')", encoding="utf-8")
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        package = kwargs["files"]["package"][1]
        with sf.tarfile.open(fileobj=sf.io.BytesIO(package), mode="r:gz") as tar:
            names = tar.getnames()
        assert "static/js/main.js" in names
        assert "_next/static/chunks/app.js" in names
        return {"ok": True, "project_id": "root_generated"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "root_generated"
    assert calls[0][1] == "/api/codex/projects/upload"


def test_project_submit_packages_github_corpus_nested_frontend_dirs(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "github-corpus"
    (root / "frontend" / "dist" / "assets").mkdir(parents=True)
    (root / "client" / "dist").mkdir(parents=True)
    (root / "apps" / "web" / "out" / "_next" / "static" / "chunks").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: github_corpus\nname: GitHub Corpus\nkind: web_static\n",
        encoding="utf-8",
    )
    (root / "frontend" / "dist" / "index.html").write_text('<script src="/assets/app.js"></script>', encoding="utf-8")
    (root / "frontend" / "dist" / "assets" / "app.js").write_text("console.log('frontend')", encoding="utf-8")
    (root / "client" / "dist" / "index.html").write_text("<html>client</html>", encoding="utf-8")
    (root / "apps" / "web" / "out" / "index.html").write_text('<script src="/_next/static/chunks/app.js"></script>', encoding="utf-8")
    (root / "apps" / "web" / "out" / "_next" / "static" / "chunks" / "app.js").write_text("console.log('apps')", encoding="utf-8")
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        manifest = json.loads(kwargs["data"]["manifest_json"])
        assert manifest["entry"] == "frontend/dist/index.html"
        package = kwargs["files"]["package"][1]
        with sf.tarfile.open(fileobj=sf.io.BytesIO(package), mode="r:gz") as tar:
            names = tar.getnames()
        assert "frontend/dist/index.html" in names
        assert "frontend/dist/assets/app.js" in names
        assert "client/dist/index.html" in names
        assert "apps/web/out/_next/static/chunks/app.js" in names
        return {"ok": True, "project_id": "github_corpus"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "github_corpus"
    assert calls[0][1] == "/api/codex/projects/upload"


def test_project_submit_auto_builds_unbuilt_source_and_uploads_dist_entry(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "vite-source"
    (root / "src").mkdir(parents=True)
    (root / "projectforge.yaml").write_text(
        "project_id: vite_source\nname: Vite Source\nkind: web_static\nentry: index.html\n",
        encoding="utf-8",
    )
    (root / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}), encoding="utf-8")
    (root / "index.html").write_text(
        '<html><body><div id="app"></div><script type="module" src="/src/main.ts"></script></body></html>',
        encoding="utf-8",
    )
    (root / "src" / "main.ts").write_text("console.log('source')", encoding="utf-8")
    build_calls = []
    calls = []

    def fake_build(build_root):
        build_calls.append(build_root)
        (root / "dist").mkdir()
        (root / "dist" / "index.html").write_text("<html>built</html>", encoding="utf-8")
        return {"ok": True, "command": "npm run build", "package_manager": "npm"}

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        manifest = json.loads(kwargs["data"]["manifest_json"])
        assert manifest["entry"] == "dist/index.html"
        assert "sf_auto_build_json" in kwargs["data"]
        package = kwargs["files"]["package"][1]
        with sf.tarfile.open(fileobj=sf.io.BytesIO(package), mode="r:gz") as tar:
            names = tar.getnames()
        assert "dist/index.html" in names
        return {"ok": True, "project_id": "vite_source"}

    monkeypatch.setattr(sf, "run_project_build", fake_build)
    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert build_calls == [root.resolve()]
    assert json.loads(capsys.readouterr().out)["project_id"] == "vite_source"
    assert calls[0][1] == "/api/codex/projects/upload"


def test_project_submit_external_url_uses_codex_auto_register(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    root = tmp_path / "external-submit"
    root.mkdir()
    (root / "projectforge.yaml").write_text(
        "project_id: external_submit\nname: 外部提交\nkind: external_web\nentry_url: https://example.com/app\ncapabilities:\n  - ai.generate\n",
        encoding="utf-8",
    )
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        assert "files" not in kwargs or kwargs["files"] is None
        assert kwargs["json_body"]["manifest"]["project_id"] == "external_submit"
        assert kwargs["json_body"]["package_hash"].startswith("sha256:")
        return {"ok": True, "project_id": "external_submit"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_submit(SimpleNamespace(path=str(root)))

    assert json.loads(capsys.readouterr().out)["project_id"] == "external_submit"
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/codex/projects/auto-register"
    assert kwargs["json_body"]["source"] == "sf_project_submit"


def test_project_status_and_logs_cli_use_codex_project_routes(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "path": path}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_status(SimpleNamespace(project_id="proj_1"))
    sf.project_logs(SimpleNamespace(project_run_id="prun_1"))
    sf.project_logs(SimpleNamespace(project_run_id="prun_2", limit=25, offset=50))
    sf.project_logs(SimpleNamespace(project_run_id="prun_3", limit=25, offset=0, ingress_cursor="cur-a", capability_cursor="cur-b"))
    sf.project_invoke(
        SimpleNamespace(
            project_id="proj_1",
            request_id="invoke-1",
            input_json='{"query":"hello"}',
            input_file="",
            params_json='{"department":"AI"}',
            prompt="请执行项目服务",
            capability="ai.cheap.generate",
            output_json="",
            report_json=[],
            todo_json=[],
            proof_json=[],
            analysis_prompt="",
            no_auto_analyze=True,
        )
    )

    output = capsys.readouterr().out
    assert "/api/codex/projects/proj_1/status" in output
    assert "/api/codex/projects/runs/prun_1/logs" in output
    assert "/api/codex/projects/runs/prun_2/logs?limit=25&offset=50" in output
    assert "/api/codex/projects/runs/prun_3/logs?limit=25&ingress_cursor=cur-a&capability_cursor=cur-b" in output
    assert "/api/codex/projects/proj_1/service/invoke" in output
    assert calls[0][1] == "/api/codex/projects/proj_1/status"
    assert calls[1][1] == "/api/codex/projects/runs/prun_1/logs"
    assert calls[2][1] == "/api/codex/projects/runs/prun_2/logs?limit=25&offset=50"
    assert calls[3][1] == "/api/codex/projects/runs/prun_3/logs?limit=25&ingress_cursor=cur-a&capability_cursor=cur-b"
    assert calls[4][0] == "POST"
    assert calls[4][1] == "/api/codex/projects/proj_1/service/invoke"
    assert calls[4][2]["json_body"]["input"] == {"query": "hello"}
    assert calls[4][2]["json_body"]["params"] == {"department": "AI"}
    assert calls[4][2]["json_body"]["auto_analyze"] is False


def test_project_asset_cli_upload_and_list_use_codex_project_routes(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    asset = tmp_path / "opening-frame.png"
    asset.write_bytes(b"png")
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "path": path}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.project_asset_upload(
        SimpleNamespace(
            project_run_id="prun_asset_1",
            file=str(asset),
            name="",
            mime_type="",
            metadata_json='{"source":"pytest"}',
            role="material_a",
            material_role="material_a",
            video_role="",
            asset_type="visual_frame",
            frame_time="2.5",
            frame_label="opening",
            source_file="demo-a.mp4",
        )
    )
    sf.project_asset_list(SimpleNamespace(project_run_id="prun_asset_1"))

    output = capsys.readouterr().out
    assert "/api/codex/projects/runs/prun_asset_1/assets" in output
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/codex/projects/runs/prun_asset_1/assets"
    upload_kwargs = calls[0][2]
    metadata = json.loads(upload_kwargs["data"]["metadata_json"])
    assert metadata["source"] == "pytest"
    assert metadata["role"] == "material_a"
    assert metadata["material_role"] == "material_a"
    assert metadata["asset_type"] == "visual_frame"
    assert metadata["frame_time"] == 2.5
    assert metadata["frame_label"] == "opening"
    assert metadata["source_file"] == "demo-a.mp4"
    assert upload_kwargs["files"]["file"][0] == "opening-frame.png"
    assert upload_kwargs["files"]["file"][2] == "image/png"
    assert calls[1] == ("GET", "/api/codex/projects/runs/prun_asset_1/assets", {})


def test_run_candidate_cli_calls_training_candidate_endpoint(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "train_1", "status": "awaiting_review"}

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.run_candidate(SimpleNamespace(
        run_id="run-1",
        title="",
        job_type="lora",
        training_strategy="",
        target_gateway_id="",
        dataset_ref="",
        objective="提升运行稳定性",
        risk_level="",
        model_family="skill-1:run_trace_improvement",
        analysis_summary="AI 复盘摘要",
        analysis_model="deepseek-v4-pro",
        analysis_prompt_hash="hash-1",
        raw_counts='{"execution_steps":1,"decision_logs":1}',
        eval_gate="",
        base_model="",
        target_metric="",
        estimated_gpu_hours=None,
        gpu_estimate="",
        forbidden_action=[],
        risk_notes="",
        allow_sandbox=False,
    ))

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "train_1"
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/training/runs/run-1/candidate"
    body = kwargs["json_body"]
    assert body["objective"] == "提升运行稳定性"
    assert body["model_family"] == "skill-1:run_trace_improvement"
    assert body["analysis_prompt_hash"] == "hash-1"
    assert body["raw_counts"] == {"execution_steps": 1, "decision_logs": 1}


def test_training_resources_cli_formats_gateway_summary(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "context": {"department": "EC", "can_view_all": False},
            "stats": {"training_gateways": 1, "gpu_count": 2, "idle_gpu_count": 1, "active_training_jobs": 1},
            "items": [{
                "id": "platform-training-1",
                "name": "平台训练 Agent",
                "department": "AI",
                "route_scope": "platform_fallback",
                "online": True,
                "gpu_count": 2,
                "idle_gpu_count": 1,
                "vram_total_gb": 48,
                "vram_free_gb": 32,
                "training": {"gateway": True, "supported_tasks": ["lora", "eval"]},
                "active_training_jobs": [{
                    "id": "train-1",
                    "title": "EC LoRA 训练",
                    "status": "running",
                }],
            }],
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_resources(SimpleNamespace(json=False))

    output = capsys.readouterr().out
    assert "SkillForge 训练资源" in output
    assert "平台训练 Agent" in output
    assert "[平台兜底]" in output
    assert "运行中: train-1" in output
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/api/training/resources"


def test_training_datasets_cli_formats_data_assets(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "total": 1,
            "stats": {
                "ready": 0,
                "with_samples": 1,
                "can_create_candidate": 0,
                "sft_samples": 80,
                "action_outcome_samples": 80,
            },
            "items": [{
                "skill_id": "skill-recommend",
                "skill_name": "商品推荐",
                "department": "EC",
                "passed": False,
                "can_create_candidate": False,
                "sample_counts": {
                    "sft_samples": 80,
                    "preference_samples": 20,
                    "action_outcome_samples": 80,
                    "eval_samples": 20,
                },
                "checks": [
                    {"key": "sft_samples", "label": "SFT 样本", "actual": 80, "threshold": 100, "passed": False},
                    {"key": "recent_failure_rate", "label": "最近 7 天失败率", "actual": 0.1, "threshold": 0.4, "passed": True},
                ],
            }],
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_datasets(SimpleNamespace(json=False))

    output = capsys.readouterr().out
    assert "SkillForge 训练数据资产" in output
    assert "skill-recommend [待补齐/不可候选]" in output
    assert "SFT 80 | 偏好 20 | 动作 80 | 评估 20" in output
    assert "缺口 SFT 样本" in output
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/api/training/datasets"


def test_training_readiness_cli_calls_skill_readiness(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "skill_id": "skill-recommend",
            "passed": True,
            "dataset_ref": "decision-log://skill-recommend/action-outcome/latest",
            "sample_counts": {
                "sft_samples": 120,
                "preference_samples": 60,
                "action_outcome_samples": 120,
                "eval_samples": 60,
            },
            "checks": [
                {"key": "sft_samples", "label": "SFT 样本", "actual": 120, "threshold": 100, "passed": True},
            ],
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_readiness(SimpleNamespace(skill_id="skill-recommend", json=False))

    output = capsys.readouterr().out
    assert "训练候选数据 skill-recommend" in output
    assert "状态: 已达标" in output
    assert "SFT 样本: 通过" in output
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/api/training/skills/skill-recommend/candidate-readiness"


def test_training_skill_candidate_cli_posts_candidate_endpoint(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "id": "train-candidate",
            "title": "商品推荐候选",
            "department": "EC",
            "status": "awaiting_review",
            "job_type": "lora",
            "target_gateway_id": "node-1",
            "dataset_ref": "decision-log://skill-recommend/action-outcome/latest",
            "training_plan": {"model_name": "skill-recommend:action_ranker", "runtime": {"progress": 0}},
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_skill_candidate(SimpleNamespace(
        skill_id="skill-recommend",
        title="商品推荐候选",
        job_type="lora",
        training_strategy="lora_task_parallel",
        target_gateway_id="node-1",
        dataset_ref="",
        objective="用历史动作结果训练推荐模型",
        risk_level="R2",
        model_family="skill-recommend:action_ranker",
        eval_gate='{"min_win_rate":0.55}',
        json=False,
    ))

    output = capsys.readouterr().out
    assert "训练任务 train-candidate" in output
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/training/skills/skill-recommend/candidate"
    body = kwargs["json_body"]
    assert body["target_gateway_id"] == "node-1"
    assert body["objective"] == "用历史动作结果训练推荐模型"
    assert body["model_family"] == "skill-recommend:action_ranker"
    assert body["eval_gate"] == {"min_win_rate": 0.55}


def test_training_jobs_cli_filters_and_formats(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "total": 1,
            "stats": {"awaiting_review": 0, "queued": 0, "running": 1, "completed": 0, "failed": 0},
            "items": [{
                "id": "train-1",
                "title": "训练商品动作模型",
                "department": "EC",
                "status": "running",
                "job_type": "lora",
                "target_gateway_id": "node-1",
                "gateway_routing": {"scope": "department"},
                "training_plan": {
                    "model_name": "item-action-ranker",
                    "runtime": {"progress": 0.5, "estimated_duration_seconds": 7200},
                },
            }],
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_jobs(SimpleNamespace(status="running", gateway="node-1", target_gateway_id="", json=False))

    output = capsys.readouterr().out
    assert "SkillForge 训练任务" in output
    assert "train-1 [训练中]" in output
    assert "模型 item-action-ranker" in output
    assert "进度 50%" in output
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/api/training/jobs?status=running&target_gateway_id=node-1"


def test_training_action_cli_posts_to_training_endpoint(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "id": "train-1",
            "title": "训练商品动作模型",
            "department": "EC",
            "status": "queued",
            "job_type": "lora",
            "target_gateway_id": "node-1",
            "training_plan": {"model_name": "item-action-ranker", "runtime": {"progress": 0}},
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_action(SimpleNamespace(training_action="approve", job_id="train-1", json=False))

    output = capsys.readouterr().out
    assert "训练任务 train-1" in output
    assert "状态: 已排队" in output
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/training/jobs/train-1/approve"


def test_training_collect_due_cli_posts_batch_collect(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "total": 2,
            "stale_seconds": 0,
            "max_jobs": 10,
            "target_gateway_id": "node-1",
            "stats": {"collected": 1, "skipped": 1, "failed": 0},
            "items": [
                {"job_id": "train-1", "status": "collected", "result_status": "completed"},
                {"job_id": "train-2", "status": "skipped", "reason": "not_dispatched"},
            ],
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_collect_due(SimpleNamespace(
        stale_seconds=0,
        max_jobs=10,
        gateway="node-1",
        target_gateway_id="",
        json=False,
    ))

    output = capsys.readouterr().out
    assert "SkillForge 训练结果批量同步" in output
    assert "已同步: 1" in output
    assert "train-2 [skipped]" in output
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/training/jobs/collect-due"
    assert kwargs["json_body"] == {"stale_seconds": 0, "max_jobs": 10, "target_gateway_id": "node-1"}


def test_training_create_cli_posts_structured_training_job(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "id": "train-new",
            "title": "训练新模型",
            "department": "EC",
            "status": "awaiting_review",
            "job_type": "lora",
            "target_gateway_id": "node-1",
            "training_plan": {
                "model_name": "item-ranker-v2",
                "base_model": "Qwen3",
                "parameters": {"epochs": 3, "learning_rate": "2e-4", "rank": 16},
                "runtime": {"progress": 0},
            },
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_create(SimpleNamespace(
        title="训练新模型",
        department="EC",
        job_type="lora",
        training_strategy="lora_task_parallel",
        target_skill_id="skill-recommend",
        target_gateway_id="node-1",
        dataset_ref="dataset://ec/actions/v1",
        objective="提升推荐准确率",
        risk_level="R2",
        spec="",
        model_name="item-ranker-v2",
        base_model="Qwen3",
        estimated_gpu_hours=1.5,
        epochs="3",
        learning_rate="2e-4",
        batch_size="",
        lora_rank="",
        max_steps="",
        param=["rank=16"],
        eval_gate='{"min_win_rate":0.55}',
        gpu_estimate="",
        json=False,
    ))

    output = capsys.readouterr().out
    assert "训练任务 train-new" in output
    method, path, kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/training/jobs"
    body = kwargs["json_body"]
    assert body["department"] == "EC"
    assert body["target_gateway_id"] == "node-1"
    assert body["spec"]["model_name"] == "item-ranker-v2"
    assert body["spec"]["base_model"] == "Qwen3"
    assert body["spec"]["estimated_gpu_hours"] == 1.5
    assert body["spec"]["parameters"] == {"epochs": "3", "learning_rate": "2e-4", "rank": 16}
    assert body["spec"]["eval_gate"] == {"min_win_rate": 0.55}


def test_training_deploy_request_and_deployment_action_cli(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "deployment": {
                "id": "deploy-1",
                "job_id": "train-1",
                "department": "EC",
                "model_family": "item-ranker-v2",
                "artifact_id": "1:0",
                "artifact_ref": {"sha256": "a" * 64},
                "target_skill_ids": ["skill-recommend"],
                "status": "awaiting_review",
                "rollout_percent": 10,
            },
            "job": {
                "id": "train-1",
                "title": "训练新模型",
                "status": "completed",
                "target_gateway_id": "node-1",
                "training_plan": {"runtime": {"progress": 1}},
            },
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_deploy_request(SimpleNamespace(
        job_id="train-1",
        target_skill_id=["skill-recommend"],
        target_skill_ids="",
        model_family="item-ranker-v2",
        rollout_percent=10,
        reason="评估通过，进入灰度",
        json=False,
    ))
    sf.training_deployment_action(SimpleNamespace(
        deployment_action="approve",
        deployment_id="deploy-1",
        json=False,
    ))
    sf.training_deployment_action(SimpleNamespace(
        deployment_action="reject",
        deployment_id="deploy-1",
        reason="评估窗口业务指标不稳定，暂不进入灰度",
        json=False,
    ))

    output = capsys.readouterr().out
    assert "模型部署 deploy-1" in output
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/training/jobs/train-1/deploy-request"
    assert calls[0][2]["json_body"]["target_skill_ids"] == ["skill-recommend"]
    assert calls[0][2]["json_body"]["rollout_percent"] == 10
    assert calls[1][0] == "POST"
    assert calls[1][1] == "/api/training/deployments/deploy-1/approve"
    assert calls[2][0] == "POST"
    assert calls[2][1] == "/api/training/deployments/deploy-1/reject"
    assert calls[2][2]["json_body"]["reason"] == "评估窗口业务指标不稳定，暂不进入灰度"


def test_training_deployments_cli_formats_registry(monkeypatch, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "total": 1,
            "stats": {"awaiting_review": 0, "canary": 1, "active": 0, "rolled_back": 0, "failed": 0},
            "items": [{
                "id": "deploy-1",
                "job_id": "train-1",
                "department": "EC",
                "model_family": "item-ranker-v2",
                "artifact_ref": {"sha256": "b" * 64},
                "target_skill_ids": ["skill-recommend"],
                "status": "canary",
                "rollout_percent": 10,
                "job": {"status": "completed"},
            }],
        }

    monkeypatch.setattr(sf, "api_request", fake_api_request)
    sf.training_deployments(SimpleNamespace(status="canary", json=False))

    output = capsys.readouterr().out
    assert "SkillForge 模型部署" in output
    assert "deploy-1 [灰度]" in output
    assert "sha bbbbbbbbbbbb" in output
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/api/training/deployments?status=canary"


def test_training_artifact_download_cli_writes_file(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    calls = []

    def fake_api_download(path, **kwargs):
        calls.append((path, kwargs))
        return b"model-bytes", {
            "content-disposition": 'attachment; filename="adapter.bin"',
            "x-skillforge-artifact-sha256": "c" * 64,
            "content-type": "application/octet-stream",
        }

    monkeypatch.setattr(sf, "api_download", fake_api_download)
    target = tmp_path / "model.bin"
    sf.training_artifact_download(SimpleNamespace(
        job_id="train-1",
        artifact_id="1:0",
        output=str(target),
        json=False,
    ))

    output = capsys.readouterr().out
    assert "已下载训练产物" in output
    assert target.read_bytes() == b"model-bytes"
    assert calls[0][0] == "/api/training/jobs/train-1/artifacts/1:0/download"


def test_update_check_base_url_arg_is_transient_by_default(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    home = tmp_path / "home"
    config_path = home / ".skillforge" / "codex-cli.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"base_url":"http://skillforge.example.com","catalog":{"old":true}}',
        encoding="utf-8",
    )
    plugin_root = home / "plugins" / "skillforge-codex"
    manifest = {
        "plugin_update": {
            "name": "skillforge-codex",
            "latest_version": "9.9.9",
            "bundle_url": "/bundle.tar.gz",
            "sha256": "sha256:" + "a" * 64,
            "format": "tar.gz",
        }
    }

    def fake_api_request(method, path, **kwargs):
        assert sf.base_url() == "http://127.0.0.1:8000"
        assert method == "GET"
        assert path == "/api/codex/catalog/manifest"
        return manifest

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf, "CONFIG_PATH", config_path)
    monkeypatch.setattr(sf, "api_request", fake_api_request)

    try:
        assert sf.main([
            "update",
            "--check",
            "--target",
            str(plugin_root),
            "--base-url",
            "http://127.0.0.1:8000/",
        ]) == 0
    finally:
        sf.set_runtime_base_url("")

    payload = json.loads(capsys.readouterr().out)
    stored = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["base_url"] == "http://127.0.0.1:8000"
    assert payload["base_url_source"] == "arg"
    assert payload["persistent_base_url"] == "http://skillforge.example.com"
    assert payload["manifest_cached"] is False
    assert stored == {"base_url": "http://skillforge.example.com", "catalog": {"old": True}}


def test_update_check_can_explicitly_persist_base_url_override(monkeypatch, tmp_path, capsys):
    sf = _load_sf_module()
    home = tmp_path / "home"
    config_path = home / ".skillforge" / "codex-cli.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"base_url":"http://skillforge.example.com"}', encoding="utf-8")
    plugin_root = home / "plugins" / "skillforge-codex"
    manifest = {
        "plugin_update": {
            "name": "skillforge-codex",
            "latest_version": "9.9.9",
            "bundle_url": "/bundle.tar.gz",
            "sha256": "sha256:" + "b" * 64,
            "format": "tar.gz",
        }
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sf, "CONFIG_DIR", home / ".skillforge")
    monkeypatch.setattr(sf, "CONFIG_PATH", config_path)
    monkeypatch.setattr(sf, "api_request", lambda *args, **kwargs: manifest)

    try:
        assert sf.main([
            "update",
            "--check",
            "--target",
            str(plugin_root),
            "--base-url",
            "http://127.0.0.1:8000",
            "--persist-base-url",
        ]) == 0
    finally:
        sf.set_runtime_base_url("")

    payload = json.loads(capsys.readouterr().out)
    stored = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["manifest_cached"] is True
    assert stored["base_url"] == "http://127.0.0.1:8000"
    assert stored["catalog"] == manifest
