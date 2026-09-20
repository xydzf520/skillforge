"""契约 schema 校验单元测试。

覆盖：
- lint_output_schema 对过严/过松/合法 schema 的判定
- validate_output 对缺 required / 类型错 / 通过的判定
- infer_schema_from_samples 从 N 样本反推 schema 的保守性
- verify_schema_phase 端到端（生成时 P0 闸门）四种负面 + 一种正面
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.common.contract_schema import (
    REPORT_ITEM_SCHEMA,
    TODO_ITEM_SCHEMA,
    build_verified_preview,
    get_preview_input,
    get_output_schema,
    infer_schema_from_samples,
    lint_runtime_data_acquisition,
    lint_output_schema,
    load_sample_input,
    normalize_output_schema_platform_fields,
    normalize_skill_bundle_files,
    parse_stdout_json,
    render_skill_md_output_section,
    sync_skill_md_output_section,
    validate_output,
)


# ===== lint_output_schema =====

def test_lint_accepts_valid_schema():
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
    assert lint_output_schema(schema) == []


def test_lint_rejects_non_object_root():
    errs = lint_output_schema({"type": "string"})
    assert any("object" in e for e in errs)


def test_lint_rejects_empty_required():
    errs = lint_output_schema({"type": "object", "properties": {}})
    assert any("required" in e for e in errs)


def test_lint_rejects_malformed_draft07():
    # "properties" 必须是 dict，给 list 会炸
    errs = lint_output_schema({"type": "object", "required": ["x"], "properties": ["bad"]})
    assert any("Draft-07" in e for e in errs)


def test_lint_rejects_bare_todos_array_schema():
    errs = lint_output_schema({
        "type": "object",
        "required": ["todos"],
        "properties": {"todos": {"type": "array"}},
    })
    assert any("items" in e and "todos" in e for e in errs)


def test_lint_accepts_structured_platform_schemas():
    schema = {
        "type": "object",
        "required": ["todos", "reports"],
        "properties": {
            "todos": {"type": "array", "items": TODO_ITEM_SCHEMA},
            "reports": {"type": "array", "items": REPORT_ITEM_SCHEMA},
        },
    }
    assert lint_output_schema(schema) == []


def test_normalize_output_schema_platform_fields_repairs_platform_contracts():
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["summary", "todos"],
            "properties": {
                "summary": {"type": "string"},
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["kind", "title"],
                        "properties": {
                            "kind": {"type": "string"},
                            "title": {"type": "string"},
                        },
                    },
                },
            },
        }
    }

    normalized = normalize_output_schema_platform_fields(contract)
    schema = normalized["output_schema"]

    assert "reports" in schema["required"]
    assert "todos" in schema["required"]
    assert "tasks" in schema["properties"]["todos"]["items"]["properties"]
    assert "callback" in schema["properties"]["todos"]["items"]["properties"]
    assert "content_markdown" in schema["properties"]["reports"]["items"]["properties"]
    assert lint_output_schema(schema) == []


# ===== runtime data acquisition lint =====

def test_lint_runtime_data_acquisition_requires_sdk_for_datasource():
    contract = {
        "input": [
            {
                "name": "生意参谋_店铺排行榜",
                "type": "json",
                "source": "datasource",
                "required": True,
            }
        ]
    }
    main_py = (
        "def main(payload):\n"
        "    rows = payload.get('生意参谋_店铺排行榜', [])\n"
        "    return {'rows': rows}\n"
    )
    errs = lint_runtime_data_acquisition(contract, main_py)
    assert any("真实采集逻辑" in e for e in errs)


def test_lint_runtime_data_acquisition_accepts_skillforge_sdk_fetch():
    contract = {
        "input": [
            {
                "name": "生意参谋_店铺排行榜",
                "type": "json",
                "source": "datasource",
                "required": True,
            }
        ]
    }
    main_py = (
        "from skillforge_sdk import SkillForge\n"
        "def collect_inputs(payload):\n"
        "    if payload.get('生意参谋_店铺排行榜'):\n"
        "        return payload\n"
        "    sf = SkillForge('skill-id')\n"
        "    payload['生意参谋_店铺排行榜'] = sf.fetch_api('https://sycm.taobao.com/x.json')\n"
        "    return payload\n"
    )
    assert lint_runtime_data_acquisition(contract, main_py) == []


def test_lint_runtime_data_acquisition_rejects_silent_empty_fetch_fallback():
    contract = {
        "input": [
            {
                "name": "生意参谋_市场排行",
                "type": "json",
                "source": "datasource",
                "required": True,
            }
        ]
    }
    main_py = (
        "from skillforge_sdk import SkillForge\n"
        "def collect_inputs(payload):\n"
        "    sf = SkillForge('skill-id')\n"
        "    try:\n"
        "        payload['生意参谋_市场排行'] = sf.fetch_api('https://sycm.taobao.com/x.json')\n"
        "    except Exception:\n"
        "        return {}\n"
        "    return payload\n"
    )

    errs = lint_runtime_data_acquisition(contract, main_py)

    assert any("静默返回空" in e for e in errs)


def test_lint_runtime_data_acquisition_rejects_direct_dingtalk_or_http_client():
    contract = {"input": [{"name": "人工上传表格", "source": "user"}]}
    main_py = (
        "import requests\n"
        "from dingtalk_sdk import Client\n"
        "def main(payload):\n"
        "    return {'reports': [], 'todos': []}\n"
    )

    errs = lint_runtime_data_acquisition(contract, main_py)

    assert any("禁止直接 import 'requests'" in e for e in errs)
    assert any("禁止直接 from 'dingtalk_sdk' import" in e for e in errs)


def test_lint_runtime_data_acquisition_rejects_fixture_file_reads():
    contract = {
        "input": [
            {
                "name": "生意参谋_店铺排行榜",
                "type": "json",
                "source": "datasource",
                "required": True,
            }
        ]
    }
    main_py = (
        "from pathlib import Path\n"
        "from skillforge_sdk import SkillForge\n"
        "def main(payload):\n"
        "    # sample_input.json is only mentioned in a comment below this test.\n"
        "    sample = Path('fixtures/sample_input.json').read_text()\n"
        "    sf = SkillForge('skill-id')\n"
        "    return {'raw': sample, 'api': sf.fetch_api('https://sycm.taobao.com/x.json')}\n"
    )
    errs = lint_runtime_data_acquisition(contract, main_py)
    assert any("不允许读取" in e for e in errs)


def test_lint_runtime_data_acquisition_ignores_user_input_skills():
    contract = {
        "input": [
            {"name": "人工上传表格", "type": "json", "source": "user", "required": True}
        ]
    }
    main_py = "def main(payload):\n    return {'ok': True}\n"
    assert lint_runtime_data_acquisition(contract, main_py) == []


def test_lint_runtime_data_acquisition_requires_sdk_for_external_url_in_skill_md():
    contract = {"input": []}
    main_py = "def main(payload):\n    return {'title': 'x'}\n"
    skill_md = "## 数据来源\n- Base URL: https://openapi.yuyidata.com/openapi/v3/\n"

    errs = lint_runtime_data_acquisition(contract, main_py, skill_md_text=skill_md)
    assert any("外部接口" in e and "SkillForge SDK" in e for e in errs)


def test_lint_runtime_data_acquisition_rejects_fixture_reads_even_without_datasource():
    contract = {"input": [{"name": "人工上传表格", "source": "user"}]}
    main_py = (
        "from pathlib import Path\n"
        "def main(payload):\n"
        "    return {'raw': Path('fixtures/sample_input.json').read_text()}\n"
    )

    errs = lint_runtime_data_acquisition(contract, main_py)
    assert any("不允许读取" in e for e in errs)


# ===== validate_output =====

def test_validate_accepts_conforming_output():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    assert validate_output({"a": "hi"}, schema) == []


def test_validate_catches_missing_required():
    schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {"a": {"type": "string"}, "b": {"type": "number"}},
    }
    errs = validate_output({"a": "hi"}, schema)
    assert len(errs) == 1
    assert "b" in errs[0]


def test_validate_catches_wrong_type():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "number"}}}
    errs = validate_output({"a": "not-a-number"}, schema)
    assert len(errs) == 1
    assert "number" in errs[0].lower() or "type" in errs[0].lower()


# ===== get_output_schema =====

def test_get_output_schema_top_level():
    schema = {"type": "object", "required": ["x"]}
    assert get_output_schema({"output_schema": schema}) == schema


def test_get_output_schema_nested():
    schema = {"type": "object", "required": ["x"]}
    assert get_output_schema({"output": {"output_schema": schema}}) == schema


def test_get_output_schema_missing():
    assert get_output_schema({"output": {}}) is None
    assert get_output_schema({}) is None
    assert get_output_schema(None) is None


def test_get_preview_input_prefers_sample_input_file_over_legacy_fixture():
    contract = {
        "fixtures": [
            {"title": "旧 fixture", "input": {"date": "2026-01-01"}},
        ]
    }
    files = {"fixtures/sample_input.json": '{"date": "2026-04-15", "shop_id": "TM001"}'}
    assert get_preview_input(contract, files) == {"date": "2026-04-15", "shop_id": "TM001"}


def test_render_skill_md_output_section_uses_required_order_and_skips_platform_fields():
    schema = {
        "type": "object",
        "required": ["字段B", "todos", "字段A", "reports", "诊断报告"],
        "properties": {
            "字段A": {"type": "array"},
            "字段B": {"type": "string"},
            "todos": {"type": "array"},
            "reports": {"type": "array"},
            "诊断报告": {"type": "string"},
        },
    }
    rendered = render_skill_md_output_section(
        schema,
        {"字段A": "A 说明", "字段B": "B 说明", "todos": "平台字段"},
    )
    assert rendered.index("- 字段B: B 说明") < rendered.index("- 字段A: A 说明")
    assert "todos" not in rendered
    assert "reports" not in rendered
    assert "诊断报告" not in rendered


def test_sync_skill_md_output_section_replaces_existing_block():
    skill_md = (
        "## 目的\n说明\n\n"
        "## 输出定义\n"
        "- 旧字段: 旧说明\n\n"
        "## 测试用例\n1. **case**: ok\n"
    )
    schema = {
        "type": "object",
        "required": ["新字段"],
        "properties": {"新字段": {"type": "string"}},
    }
    synced = sync_skill_md_output_section(skill_md, schema, {"新字段": "新说明"})
    assert "- 新字段: 新说明" in synced
    assert "旧字段" not in synced


def test_normalize_skill_bundle_files_adds_todo_section_for_declared_todos():
    files = {
        "SKILL.md": "## 目的\n说明\n\n## 测试用例\n1. **case**: ok\n",
    }
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["summary", "todos"],
            "properties": {
                "summary": {"type": "string"},
                "todos": {"type": "array", "items": TODO_ITEM_SCHEMA},
            },
        },
        "output": {"schema": {"summary": "摘要"}},
    }

    normalized = normalize_skill_bundle_files(files, contract)

    assert "## 待办" in normalized["SKILL.md"]
    assert '"kind": "dispatch"' in normalized["SKILL.md"]
    assert "output.todos" in normalized["SKILL.md"]
    assert normalized["SKILL.md"].index("## 待办") < normalized["SKILL.md"].index("## 测试用例")


def test_normalize_skill_bundle_files_keeps_existing_todo_section():
    files = {
        "SKILL.md": (
            "## 目的\n说明\n\n"
            "## 待办\n```json\n[{\"kind\":\"review\",\"title\":\"人工确认\"}]\n```\n"
        ),
    }
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["todos"],
            "properties": {"todos": {"type": "array", "items": TODO_ITEM_SCHEMA}},
        },
    }

    normalized = normalize_skill_bundle_files(files, contract)

    assert normalized["SKILL.md"].count("## 待办") == 1
    assert "人工确认" in normalized["SKILL.md"]


# ===== parse_stdout_json =====

def test_parse_stdout_clean_json():
    parsed, err = parse_stdout_json('{"a": 1}')
    assert parsed == {"a": 1}
    assert err is None


def test_parse_stdout_tolerates_prelog():
    parsed, err = parse_stdout_json('[info] starting\n{"a": 1}')
    assert parsed == {"a": 1}


def test_parse_stdout_empty():
    parsed, err = parse_stdout_json("   ")
    assert parsed is None
    assert "空" in err


def test_parse_stdout_nonjson():
    parsed, err = parse_stdout_json("completely not json")
    assert parsed is None


# ===== infer_schema_from_samples =====

def test_infer_schema_intersection_required():
    samples = [
        {"a": 1, "b": 2, "c": 3},
        {"a": 10, "b": 20},  # c missing
        {"a": 100, "b": 200, "c": 300, "d": 4},
    ]
    schema = infer_schema_from_samples(samples)
    # c 出现在 2/3 样本 — 不进 required; a 和 b 3/3 进 required
    assert set(schema["required"]) == {"a", "b"}


def test_infer_schema_type_union():
    samples = [
        {"x": "hi"},
        {"x": 42},
    ]
    schema = infer_schema_from_samples(samples)
    assert schema["properties"]["x"]["type"] == ["number", "string"]


def test_infer_schema_empty_input():
    assert infer_schema_from_samples([])["required"] == []


def test_inferred_schema_roundtrip_validates():
    samples = [{"a": "1", "b": [1, 2], "c": {"k": "v"}} for _ in range(3)]
    schema = infer_schema_from_samples(samples)
    for s in samples:
        assert validate_output(s, schema) == []


@pytest.mark.asyncio
async def test_build_verified_preview_returns_runtime_todos_and_reports():
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["reports", "todos"],
            "properties": {
                "reports": {"type": "array", "items": REPORT_ITEM_SCHEMA},
                "todos": {"type": "array", "items": TODO_ITEM_SCHEMA},
            },
        }
    }
    files = {
        "SKILL.md": "## 输出定义\n\n## 待办\n- output.todos 会进入收件待办\n",
        "scripts/main.py": (
            "import json, sys\n"
            "def main(payload):\n"
            "    return {\n"
            "      'reports': [{'channel': 'dingtalk_card', 'title': '运行报告', 'summary': '已完成', 'recipients': {'roles': ['biz_owner']}}],\n"
            "      'todos': [{'kind': 'dispatch', 'title': '处理异常', 'reviewer_role': 'operator', 'tasks': [{'content': '跟进异常项'}]}],\n"
            "    }\n"
            "if __name__ == '__main__':\n"
            "    print(json.dumps(main(json.loads(sys.stdin.read() or '{}')), ensure_ascii=False))\n"
        ),
        "fixtures/sample_input.json": "{}",
    }

    preview = await build_verified_preview(contract, files, cache_key="test-runtime-preview")

    assert preview["success"] is True
    assert preview["sandbox_counts"] == {"todos": 1, "reports": 1}
    assert preview["sandbox_output"]["todos"][0]["title"] == "处理异常"
    assert preview["sandbox_output"]["reports"][0]["title"] == "运行报告"


# ===== verify_schema_phase (E2E with temp dirs) =====

@pytest.mark.asyncio
async def test_verify_schema_phase_valid(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text("## 输出定义\n- title: 标题\n- items: 列表\n")
    (tmp_path / "scripts/main.py").write_text(
        "import json, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'title': 'x', 'items': [1]}))\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text('{"date": "2026-04-15"}')
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["title", "items"],
            "properties": {
                "title": {"type": "string"},
                "items": {"type": "array", "minItems": 1},
            },
        }
    }
    errs = await verify_schema_phase(tmp_path, contract)
    assert errs == []


@pytest.mark.asyncio
async def test_verify_schema_phase_catches_missing_key(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text("## 输出定义\n- title: 标题\n- items: 列表\n")
    # main.py 漏输出 items
    (tmp_path / "scripts/main.py").write_text(
        "import json, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'title': 'x'}))\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text("{}")
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["title", "items"],
            "properties": {
                "title": {"type": "string"},
                "items": {"type": "array"},
            },
        }
    }
    errs = await verify_schema_phase(tmp_path, contract)
    assert any("items" in e for e in errs)


@pytest.mark.asyncio
async def test_verify_schema_phase_catches_runtime_error(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text("## 输出定义\n- x: 字段\n")
    (tmp_path / "scripts/main.py").write_text(
        "raise RuntimeError('oops')\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text("{}")
    # properties[x] 必须写 type,否则会被 lint_output_schema 拦在前面 — 但这里
    # 我们要断言的是"运行时错误"被捕捉,所以得让 lint 通过,让 subprocess 真跑起来。
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["x"],
            "properties": {"x": {"type": "string"}},
        }
    }
    errs = await verify_schema_phase(tmp_path, contract)
    assert any("退出码" in e or "退出码" in e.lower() for e in errs)


@pytest.mark.asyncio
async def test_verify_schema_phase_skips_when_no_schema(tmp_path):
    """contract 缺 output_schema 应报一条清晰错误（legacy 升级路径）"""
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    errs = await verify_schema_phase(tmp_path, {"goal": "x"})
    assert any("output_schema" in e for e in errs)


@pytest.mark.asyncio
async def test_verify_schema_phase_supports_nested_output_schema(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text("## 输出定义\n- title: 标题\n")
    (tmp_path / "scripts/main.py").write_text(
        "import json, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'title': 'x'}))\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text("{}")
    contract = {
        "output": {
            "output_schema": {
                "type": "object",
                "required": ["title"],
                "properties": {"title": {"type": "string"}},
            }
        }
    }
    errs = await verify_schema_phase(tmp_path, contract)
    assert errs == []


@pytest.mark.asyncio
async def test_verify_schema_phase_catches_skill_md_format_drift(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text(
        "## 输出\n\n| 输出项 | 格式 |\n|---|---|\n| title | string |\n"
    )
    (tmp_path / "scripts/main.py").write_text(
        "import json, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'title': 'x'}))\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text("{}")
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        }
    }
    errs = await verify_schema_phase(tmp_path, contract)
    assert any("SKILL.md §输出定义" in e for e in errs)


@pytest.mark.asyncio
async def test_verify_schema_phase_catches_datasource_without_runtime_fetch(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text("## 输出定义\n- title: 标题\n")
    (tmp_path / "scripts/main.py").write_text(
        "import json, sys\n"
        "payload = json.loads(sys.stdin.read())\n"
        "rows = payload.get('生意参谋_店铺排行榜', [])\n"
        "print(json.dumps({'title': str(len(rows))}))\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text('{"生意参谋_店铺排行榜": []}')
    contract = {
        "input": [
            {
                "name": "生意参谋_店铺排行榜",
                "type": "json",
                "source": "datasource",
                "required": True,
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        },
    }
    errs = await verify_schema_phase(tmp_path, contract)
    assert any("SkillForge SDK" in e for e in errs)


@pytest.mark.asyncio
async def test_verify_schema_phase_catches_placeholder_main(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text(
        "## 输出定义\n- conclusion: 结论\n- suggested_action: 建议\n- detail: 详情\n"
    )
    (tmp_path / "scripts/main.py").write_text(
        "import json, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'conclusion': '待实现', 'suggested_action': '请补充业务逻辑', 'detail': {}}))\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text("{}")
    contract = {
        "output_schema": {
            "type": "object",
            "required": ["conclusion", "suggested_action", "detail"],
            "properties": {
                "conclusion": {"type": "string"},
                "suggested_action": {"type": "string"},
                "detail": {"type": "object"},
            },
        }
    }

    errs = await verify_schema_phase(tmp_path, contract)
    assert any("占位" in e or "待实现" in e for e in errs)


@pytest.mark.asyncio
async def test_verify_schema_phase_requires_acquisition_for_external_url(tmp_path):
    from app.coding_agent.skill_creation_runner import verify_schema_phase

    (tmp_path / "scripts").mkdir()
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "SKILL.md").write_text(
        "## 数据来源\n- Base URL: https://openapi.yuyidata.com/openapi/v3/\n\n"
        "## 输出定义\n- title: 标题\n"
    )
    (tmp_path / "scripts/main.py").write_text(
        "import json, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'title': 'x'}))\n"
    )
    (tmp_path / "fixtures/sample_input.json").write_text("{}")
    contract = {
        "input": [],
        "output_schema": {
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        },
    }

    errs = await verify_schema_phase(tmp_path, contract)
    assert any("真实采集逻辑" in e for e in errs)


# ===== run_main_with_sample env 白名单（修复 1） =====

@pytest.mark.asyncio
async def test_run_main_env_strips_secrets(tmp_path, monkeypatch):
    """敏感 env(DATABASE_URL / DINGTALK_APP_SECRET / LITELLM_API_KEY 等)必须不能
    透传给 LLM 生成的 main.py。"""
    from app.common.contract_schema import run_main_with_sample

    # 塞一堆敏感 env,模拟真实生产环境
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fakepass@localhost/db")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "super-secret-dingtalk")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-fake-litellm-xxx")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("SKILLFORGE_SESSION_SECRET", "session-secret-xxx")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "main.py").write_text(
        "import json, os, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps(dict(os.environ)))\n"
    )

    rc, stdout, stderr = await run_main_with_sample(tmp_path, {}, timeout=10.0)
    assert rc == 0, f"main.py exited non-zero: stderr={stderr}"

    child_env = json.loads(stdout)
    forbidden = {
        "DATABASE_URL",
        "DINGTALK_APP_SECRET",
        "LITELLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "SKILLFORGE_SESSION_SECRET",
    }
    leaked = forbidden & set(child_env.keys())
    assert not leaked, f"敏感 env 泄漏给 main.py: {leaked}"


@pytest.mark.asyncio
async def test_run_main_env_keeps_path(tmp_path, monkeypatch):
    """PATH 必须保留,否则 main.py 里调的 curl/openssl 都跑不起来。"""
    from app.common.contract_schema import run_main_with_sample

    monkeypatch.setenv("PATH", "/usr/bin:/bin:/sentinel-path-xxx")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "main.py").write_text(
        "import json, os, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'path': os.environ.get('PATH', '')}))\n"
    )

    rc, stdout, _stderr = await run_main_with_sample(tmp_path, {}, timeout=10.0)
    assert rc == 0
    data = json.loads(stdout)
    assert "/sentinel-path-xxx" in data["path"]


@pytest.mark.asyncio
async def test_run_main_pythonpath_is_fixed_to_scripts_and_shared_sdk(tmp_path, monkeypatch):
    """PYTHONPATH 只能包含 scripts/ 和受信任的 SDK 目录,不拼接外部值。"""
    from app.common.contract_schema import run_main_with_sample

    monkeypatch.setenv("PYTHONPATH", "/evil/path/to/app:/another")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "main.py").write_text(
        "import json, os, sys\n"
        "json.loads(sys.stdin.read())\n"
        "print(json.dumps({'pp': os.environ.get('PYTHONPATH', '')}))\n"
    )

    rc, stdout, _stderr = await run_main_with_sample(tmp_path, {}, timeout=10.0)
    assert rc == 0
    data = json.loads(stdout)
    parts = data["pp"].split(":")
    assert parts[0] == str(scripts)
    assert any(part.endswith("app/skill_runtime_sdk") for part in parts)
    assert "/evil/path" not in data["pp"]


# ===== parse_stdout_json 字符串内大括号（修复 2） =====

def test_parse_stdout_with_json_string_containing_braces():
    """JSON 字符串里出现 `{` 不应该让反向括号匹配误判。"""
    parsed, err = parse_stdout_json('log line\n{"note":"contains { inside"}')
    assert err is None, f"unexpected err: {err}"
    assert parsed == {"note": "contains { inside"}


def test_parse_stdout_prefers_last_object_when_multiple_present():
    """多个 JSON 对象时取最后一个(保持原 docstring 承诺:'最后一个 JSON 对象')。"""
    parsed, err = parse_stdout_json('{"a": 1}\n[info] middle\n{"b": 2}')
    assert err is None
    assert parsed == {"b": 2}


def test_parse_stdout_handles_escaped_quotes_in_string():
    """字符串里的转义双引号和大括号都不能骗到解析器。"""
    parsed, err = parse_stdout_json(
        'warning: something\n{"msg": "he said \\"{wow}\\""}'
    )
    assert err is None
    assert parsed == {"msg": 'he said "{wow}"'}


# ===== lint_output_schema 拒绝空 properties schema（修复 3） =====

def test_lint_output_schema_rejects_empty_property():
    errs = lint_output_schema(
        {"type": "object", "required": ["x"], "properties": {"x": {}}}
    )
    assert any("properties['x'] 必须指定" in e for e in errs), errs


def test_lint_output_schema_rejects_missing_property_entry():
    """required 字段在 properties 中完全缺失 → 同样拒绝。"""
    errs = lint_output_schema(
        {"type": "object", "required": ["x"], "properties": {}}
    )
    assert any("properties['x'] 必须指定" in e for e in errs), errs


def test_lint_output_schema_accepts_typed_property():
    errs = lint_output_schema(
        {
            "type": "object",
            "required": ["x"],
            "properties": {"x": {"type": "string"}},
        }
    )
    assert not any("必须指定" in e for e in errs), errs


def test_lint_output_schema_accepts_enum_const_ref():
    """enum / const / $ref 任一都应视为已指定类型约束。"""
    for field_schema in (
        {"enum": ["a", "b"]},
        {"const": "fixed"},
        {"$ref": "#/definitions/Foo"},
    ):
        errs = lint_output_schema(
            {
                "type": "object",
                "required": ["x"],
                "properties": {"x": field_schema},
            }
        )
        assert not any("必须指定" in e for e in errs), (field_schema, errs)
