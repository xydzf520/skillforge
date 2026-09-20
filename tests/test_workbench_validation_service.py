from app.workbench.validation_service import validate_module_patch


def test_validate_goal_patch_warns_on_short_goal():
    result = validate_module_patch("goal", {"goal": "太短了"})
    assert result["valid"] is True
    assert "目标描述过短，建议至少 10 个字符" in result["warnings"]


def test_validate_output_table_rejects_duplicate_names():
    result = validate_module_patch(
        "output_table",
        {
            "output_table": [
                {"name": "结果", "format": "text"},
                {"name": "结果", "format": "table"},
            ]
        },
    )
    assert result["valid"] is False
    assert "输出字段名称重复" in result["errors"]


def test_validate_workflow_requires_nodes():
    result = validate_module_patch("workflow", {"workflow": {"nodes": []}})
    assert result["valid"] is False
    assert "工作流至少需要 1 个节点" in result["errors"]
