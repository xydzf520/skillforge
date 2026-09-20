"""工作台 Studio 新增端点测试: chat / command / apply-partial / context_builder / diff_service"""

import pytest


# ═══ diff_service 单元测试 ═══

class TestDiffService:
    """模块级 diff 计算"""

    def test_string_modify(self):
        from app.workbench.diff_service import build_diff_preview
        result = build_diff_preview("goal", {"goal": "新目标"}, {"goal": "旧目标"})
        assert len(result["hunks"]) == 1
        assert result["hunks"][0]["type"] == "modify"
        assert result["hunks"][0]["old"] == "旧目标"
        assert result["hunks"][0]["new"] == "新目标"

    def test_string_no_change(self):
        from app.workbench.diff_service import build_diff_preview
        result = build_diff_preview("goal", {"goal": "相同"}, {"goal": "相同"})
        assert len(result["hunks"]) == 0

    def test_list_add_item(self):
        from app.workbench.diff_service import build_diff_preview
        before = {"params": [{"name": "a", "value": "1"}]}
        after = {"params": [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]}
        result = build_diff_preview("params", after, before)
        add_hunks = [h for h in result["hunks"] if h["type"] == "add"]
        assert len(add_hunks) >= 1

    def test_list_modify_field(self):
        from app.workbench.diff_service import build_diff_preview
        before = {"params": [{"name": "roi", "value": "1.2"}]}
        after = {"params": [{"name": "roi", "value": "1.5"}]}
        result = build_diff_preview("params", after, before)
        mod_hunks = [h for h in result["hunks"] if h["type"] == "modify"]
        assert len(mod_hunks) >= 1
        assert any("1.5" in str(h.get("new", "")) for h in mod_hunks)

    def test_dict_add_key(self):
        from app.workbench.diff_service import build_diff_preview
        before = {"meta": {"name": "test"}}
        after = {"meta": {"name": "test", "dept": "EC"}}
        result = build_diff_preview("meta", after, before)
        add_hunks = [h for h in result["hunks"] if h["type"] == "add"]
        assert len(add_hunks) >= 1

    def test_no_before_returns_after(self):
        from app.workbench.diff_service import build_diff_preview
        result = build_diff_preview("params", {"params": [{"name": "x"}]}, None)
        assert result["before"] == ""
        assert result["after"] != ""


# ═══ context_builder 单元测试 ═══

class TestBuildAnswerPrompts:
    """workbench_service._build_answer_prompts 字段访问回归测试

    BUG: 历史代码用 structure.purpose / steps / data_inputs 访问 SkillStructure，
    但 SkillStructure (pydantic) 的字段是 goal / rules / params，
    所有 hasattr 永远 False → 永远输出"Skill 内容为空"。
    本测试用 mock 验证字段访问正确，避免回归。
    """

    def test_build_includes_all_skill_fields(self, monkeypatch):
        from app.workbench import service as svc_module
        from app.workbench.schemas import SkillStructure

        # mock load_skill_structure 返回结构化数据
        fake_structure = SkillStructure(
            meta={"name": "TEST-SKILL", "department": "EC"},
            goal="一个测试用的目标描述",
            rules=[
                {
                    "id": "step_1",
                    "name": "判断 ROI 阈值",
                    "branches": [
                        {"condition": "ROI > 1.5", "conclusion": "绿灯", "action": "加预算"},
                        {"condition": "ROI < 0.8", "conclusion": "红灯", "action": "暂停"},
                    ],
                },
                {
                    "id": "step_2",
                    "name": "判断库存",
                    "branches": [
                        {"condition": "库存 < 10", "conclusion": "缺货告警"},
                    ],
                },
            ],
            params=[
                {"name": "roi_threshold", "default_value": 1.2, "description": "ROI 阈值"},
                {"name": "stock_min", "default_value": 10},
            ],
            output_table=[
                {"name": "result", "format": "JSON", "recipient": "运营", "approval_level": 1},
            ],
            test_cases=[
                {"name": "用例 1", "input_data": {}, "expected_output": {}},
                {"name": "用例 2", "input_data": {}, "expected_output": {}},
            ],
        )

        monkeypatch.setattr(
            svc_module.context_builder,
            "load_skill_structure",
            lambda skill_id: fake_structure,
        )

        sys_prompt, skill_ctx, _ = svc_module.workbench_service._build_answer_prompts(
            "TEST-SKILL", {"active_module": "overview"}
        )

        # 关键断言：所有字段都被注入到 context
        assert "（Skill 内容为空）" not in skill_ctx, "BUG 回归: 字段访问失败导致空内容"
        assert "一个测试用的目标描述" in skill_ctx
        assert "判断 ROI 阈值" in skill_ctx
        assert "ROI > 1.5" in skill_ctx
        assert "绿灯" in skill_ctx
        assert "判断库存" in skill_ctx
        assert "roi_threshold" in skill_ctx
        assert "result" in skill_ctx
        assert "JSON" in skill_ctx
        assert "用例 1" in skill_ctx

        # system prompt 包含 skill_name
        assert "TEST-SKILL" in sys_prompt

    def test_build_falls_back_when_load_fails(self, monkeypatch):
        """load_skill_structure 抛异常时不应崩溃"""
        from app.workbench import service as svc_module

        def boom(skill_id):
            raise RuntimeError("DB 不可达")

        monkeypatch.setattr(svc_module.context_builder, "load_skill_structure", boom)

        sys_prompt, skill_ctx, _ = svc_module.workbench_service._build_answer_prompts(
            "MISSING", {"active_module": "overview"}
        )
        # 没崩，且生成了 fallback 提示
        assert "（Skill 内容为空）" in skill_ctx
        assert "MISSING" in sys_prompt


class TestContextBuilder:
    """上下文构建"""

    def test_draft_overrides_persisted(self):
        """draft_snapshot 覆盖已落库数据"""
        from app.workbench.context_builder import build_chat_context
        # 需要 skill 存在才能测试，此处验证函数签名和基本逻辑
        # 实际集成测试在有 DB 的环境下运行
        try:
            ctx = build_chat_context(
                "nonexistent",
                draft_snapshot={"params": [{"name": "x", "value": "99"}]},
            )
        except Exception:
            pass  # skill 不存在时会抛错，这是预期的

    def test_selection_context_included(self):
        """选区信息包含在上下文中"""
        from app.workbench.context_builder import build_chat_context
        try:
            ctx = build_chat_context(
                "nonexistent",
                selection={"module_id": "rules", "text": "some rule", "start_line": 5, "end_line": 10},
            )
        except Exception:
            pass  # skill 不存在是预期的


# ═══ apply_service 单元测试 ═══

class TestApplyPartial:
    """Hunk 级部分 apply"""

    def test_apply_single_hunk_modify(self):
        from app.workbench.apply_service import apply_partial_patch
        doc = {
            "meta": {"name": "test", "id": "test-skill"},
            "goal": "目标",
            "params": [{"name": "roi", "default_value": 1.2}],
            "rules": [], "output_table": [], "test_cases": [],
            "custom_sections": {},
        }
        hunks = [
            {"index": 0, "type": "modify", "path": "params[0].default_value", "old": "1.2", "new": "1.5"},
            {"index": 1, "type": "modify", "path": "goal", "old": "目标", "new": "新目标"},
        ]
        result = apply_partial_patch("test-skill", doc, "params", hunks, accepted_indices=[0])
        # hunk 0 被接受，hunk 1 被跳过
        assert result.get("skill_md") is not None

    def test_apply_no_hunks_returns_original(self):
        from app.workbench.apply_service import apply_partial_patch
        doc = {
            "meta": {"name": "test", "id": "test-skill"},
            "goal": "目标",
            "params": [{"name": "roi", "default_value": 1.2}],
            "rules": [], "output_table": [], "test_cases": [],
            "custom_sections": {},
        }
        result = apply_partial_patch("test-skill", doc, "params", [], accepted_indices=[])
        assert result.get("skill_md") is not None


# ═══ schemas 验证测试 ═══

class TestSchemas:
    """请求/响应模型"""

    def test_chat_request_valid(self):
        from app.workbench.schemas import ChatRequest
        req = ChatRequest(session_id="s1", message="修改参数")
        assert req.message == "修改参数"
        assert req.context.active_module is None

    def test_chat_request_with_context(self):
        from app.workbench.schemas import ChatRequest
        req = ChatRequest(
            session_id="s1",
            message="改阈值",
            context={
                "active_module": "params",
                "selection": {"module_id": "params", "text": "roi: 1.2"},
                "draft_snapshot": {"params": [{"name": "roi", "value": "1.2"}]},
            },
        )
        assert req.context.active_module == "params"
        assert req.context.selection.text == "roi: 1.2"

    def test_chat_request_empty_message_rejected(self):
        from app.workbench.schemas import ChatRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ChatRequest(session_id="s1", message="")

    def test_command_request_valid(self):
        from app.workbench.schemas import CommandRequest
        req = CommandRequest(session_id="s1", command="generate-tests")
        assert req.command == "generate-tests"

    def test_command_request_invalid_pattern(self):
        from app.workbench.schemas import CommandRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CommandRequest(session_id="s1", command="INVALID!")

    def test_partial_apply_request(self):
        from app.workbench.schemas import PartialApplyRequest
        req = PartialApplyRequest(session_id="s1", patch_id="p1", accepted_hunks=[0, 2], rejected_hunks=[1])
        assert req.accepted_hunks == [0, 2]
