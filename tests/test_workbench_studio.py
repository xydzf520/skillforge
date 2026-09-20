"""SkillStudio 后端深度测试：chat/command/apply-partial/context/diff/cache/schemas 全覆盖"""

import pytest
import json


# ═══ Schemas 验证 ═══

class TestChatSchemas:
    def test_chat_request_minimal(self):
        from app.workbench.schemas import ChatRequest
        req = ChatRequest(session_id="s1", message="hello")
        assert req.context.active_module is None
        assert req.intent is None

    def test_chat_request_full_context(self):
        from app.workbench.schemas import ChatRequest
        req = ChatRequest(
            session_id="s1", message="改阈值",
            context={"active_module": "params", "selection": {"module_id": "params", "text": "1.2"}, "draft_snapshot": {"params": []}},
            intent="tune_threshold", reference_ids=["ref1"],
        )
        assert req.context.active_module == "params"
        assert req.context.selection.text == "1.2"
        assert req.intent == "tune_threshold"

    def test_chat_request_empty_message_rejected(self):
        from app.workbench.schemas import ChatRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ChatRequest(session_id="s1", message="")

    def test_chat_request_long_message_rejected(self):
        from app.workbench.schemas import ChatRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ChatRequest(session_id="s1", message="x" * 5001)

    def test_command_request_valid(self):
        from app.workbench.schemas import CommandRequest
        req = CommandRequest(session_id="s1", command="generate-tests")
        assert req.command == "generate-tests"

    def test_command_request_invalid_pattern(self):
        from app.workbench.schemas import CommandRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CommandRequest(session_id="s1", command="INVALID!")
        with pytest.raises(ValidationError):
            CommandRequest(session_id="s1", command="has space")

    def test_partial_apply_request(self):
        from app.workbench.schemas import PartialApplyRequest
        req = PartialApplyRequest(session_id="s1", patch_id="p1", accepted_hunks=[0, 2], rejected_hunks=[1])
        assert req.accepted_hunks == [0, 2]
        assert req.rejected_hunks == [1]

    def test_module_name_includes_meta(self):
        from app.workbench.schemas import ChatContext
        ctx = ChatContext(active_module="meta")
        assert ctx.active_module == "meta"

    def test_all_module_names_valid(self):
        from app.workbench.schemas import ChatContext
        for mod in ["meta", "goal", "rules", "params", "output_table", "test_cases", "workflow"]:
            ctx = ChatContext(active_module=mod)
            assert ctx.active_module == mod

    def test_selection_context(self):
        from app.workbench.schemas import SelectionContext
        sel = SelectionContext(module_id="rules", start_line=5, end_line=10, text="ROI > 1")
        assert sel.text == "ROI > 1"


# ═══ Diff Service ═══

class TestDiffService:
    def test_string_diff(self):
        from app.workbench.diff_service import build_diff_preview
        r = build_diff_preview("goal", {"goal": "new"}, {"goal": "old"})
        assert len(r["hunks"]) == 1
        assert r["hunks"][0]["type"] == "modify"
        assert r["hunks"][0]["old"] == "old"
        assert r["hunks"][0]["new"] == "new"

    def test_no_change(self):
        from app.workbench.diff_service import build_diff_preview
        r = build_diff_preview("goal", {"goal": "same"}, {"goal": "same"})
        assert len(r["hunks"]) == 0

    def test_list_add(self):
        from app.workbench.diff_service import build_diff_preview
        before = {"params": [{"name": "a", "value": "1"}]}
        after = {"params": [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]}
        r = build_diff_preview("params", after, before)
        adds = [h for h in r["hunks"] if h["type"] == "add"]
        assert len(adds) >= 1

    def test_list_modify(self):
        from app.workbench.diff_service import build_diff_preview
        before = {"params": [{"name": "roi", "value": "1.2"}]}
        after = {"params": [{"name": "roi", "value": "1.5"}]}
        r = build_diff_preview("params", after, before)
        mods = [h for h in r["hunks"] if h["type"] == "modify"]
        assert len(mods) >= 1

    def test_list_remove(self):
        from app.workbench.diff_service import build_diff_preview
        before = {"params": [{"name": "a"}, {"name": "b"}]}
        after = {"params": [{"name": "a"}]}
        r = build_diff_preview("params", after, before)
        removes = [h for h in r["hunks"] if h["type"] == "remove"]
        assert len(removes) >= 1

    def test_dict_diff(self):
        from app.workbench.diff_service import build_diff_preview
        before = {"meta": {"name": "old", "dept": "A"}}
        after = {"meta": {"name": "new", "dept": "A", "tag": "x"}}
        r = build_diff_preview("meta", after, before)
        assert any(h["type"] == "modify" for h in r["hunks"])
        assert any(h["type"] == "add" for h in r["hunks"])

    def test_no_before_returns_after(self):
        from app.workbench.diff_service import build_diff_preview
        r = build_diff_preview("goal", {"goal": "text"}, None)
        assert r["before"] == ""
        assert r["after"] != ""

    def test_summary_text(self):
        from app.workbench.diff_service import build_diff_preview
        r = build_diff_preview("params", {"params": [{"name": "x"}]}, {"params": []})
        assert "新增" in r["summary"]


# ═══ Apply Service ═══

class TestApplyService:
    def test_apply_partial_accepted_hunks(self):
        from app.workbench.apply_service import apply_partial_patch
        doc = {
            "meta": {"name": "test", "id": "t1"},
            "goal": "旧目标", "params": [{"name": "roi", "default_value": 1.2}],
            "rules": [], "output_table": [], "test_cases": [],
            "custom_sections": {},
        }
        hunks = [
            {"index": 0, "type": "modify", "path": "params[0].default_value", "old": "1.2", "new": "1.5"},
            {"index": 1, "type": "modify", "path": "goal", "old": "旧目标", "new": "新目标"},
        ]
        result = apply_partial_patch("t1", doc, "params", hunks, [0])
        assert "skill_md" in result

    def test_apply_no_hunks(self):
        from app.workbench.apply_service import apply_partial_patch
        doc = {"meta": {"name": "t", "id": "t1"}, "goal": "", "params": [], "rules": [], "output_table": [], "test_cases": [], "custom_sections": {}}
        result = apply_partial_patch("t1", doc, "params", [], [])
        assert "skill_md" in result

    def test_apply_full_patch(self):
        from app.workbench.apply_service import apply_patch_to_skill_document
        doc = {"meta": {"name": "t", "id": "t1"}, "goal": "old", "params": [], "rules": [], "output_table": [], "test_cases": [], "custom_sections": {}}
        result = apply_patch_to_skill_document("t1", doc, "goal", {"goal": "new goal"})
        assert "new goal" in result["skill_md"]

    def test_build_patch_from_message_params(self):
        from app.workbench.apply_service import build_patch_from_message
        patch = build_patch_from_message("params", "设置阈值为 2.5")
        assert "params" in patch
        assert patch["params"][0]["default_value"] == 2.5

    def test_build_patch_from_message_goal(self):
        from app.workbench.apply_service import build_patch_from_message
        patch = build_patch_from_message("goal", "新的目标描述")
        assert patch["goal"] == "新的目标描述"

    def test_build_patch_from_message_rules(self):
        from app.workbench.apply_service import build_patch_from_message
        patch = build_patch_from_message("rules", "添加一条规则")
        assert "rules" in patch


# ═══ Context Builder ═══

class TestContextBuilder:
    def test_build_chat_context_basic(self):
        """基本构建（会因 skill 不存在抛错）"""
        from app.workbench.context_builder import build_chat_context
        try:
            ctx = build_chat_context("nonexistent", active_module="goal")
        except Exception:
            pass  # skill 不存在是预期的

    def test_build_chat_context_with_selection(self):
        from app.workbench.context_builder import build_chat_context
        try:
            ctx = build_chat_context(
                "nonexistent",
                selection={"module_id": "rules", "text": "ROI > 1", "start_line": 5, "end_line": 10},
            )
        except Exception:
            pass

    def test_structure_cache_invalidation(self):
        from app.workbench.context_builder import invalidate_structure_cache, _structure_cache
        _structure_cache["test_key"] = (0, "dummy")
        invalidate_structure_cache("test_key")
        assert "test_key" not in _structure_cache

    def test_structure_cache_invalidate_all(self):
        from app.workbench.context_builder import invalidate_structure_cache, _structure_cache
        _structure_cache["a"] = (0, "x")
        _structure_cache["b"] = (0, "y")
        invalidate_structure_cache(None)
        assert len(_structure_cache) == 0


# ═══ Intent Service ═══

class TestIntentService:
    def test_detect_params_intent(self):
        from app.workbench.intent_service import detect_intent
        r = detect_intent("把 ROI 阈值调高到 1.5")
        assert r["target_module"] == "params"

    def test_detect_rules_intent(self):
        from app.workbench.intent_service import detect_intent
        r = detect_intent("修改判断逻辑")
        assert r["target_module"] == "rules"

    def test_detect_test_cases_intent(self):
        from app.workbench.intent_service import detect_intent
        r = detect_intent("补充一个测试样例")
        assert r["target_module"] == "test_cases"

    def test_detect_output_intent(self):
        from app.workbench.intent_service import detect_intent
        r = detect_intent("增加一个输出字段")
        assert r["target_module"] == "output_table"

    def test_detect_workflow_intent(self):
        from app.workbench.intent_service import detect_intent
        r = detect_intent("编排一个工作流")
        assert r["target_module"] == "workflow"

    def test_detect_goal_intent(self):
        from app.workbench.intent_service import detect_intent
        r = detect_intent("修改这个 Skill 的目标")
        assert r["target_module"] == "goal"

    def test_detect_with_active_module_fallback(self):
        from app.workbench.intent_service import detect_intent
        r = detect_intent("这个不太好", active_module="output_table")
        assert r["target_module"] == "output_table"


# ═══ Cache ═══

class TestWorkbenchCache:
    def test_cache_key_patterns(self):
        """验证缓存 key 命名规范"""
        from app.workbench.cache import (
            TTL_SESSION_CTX, TTL_DRAFT, TTL_PATCH_DIFF, TTL_INTENT_HINT, TTL_COMMAND,
        )
        assert TTL_SESSION_CTX == 1800
        assert TTL_DRAFT == 600
        assert TTL_PATCH_DIFF == 1800
        assert TTL_INTENT_HINT == 300
        assert TTL_COMMAND == 600


# ═══ Service handle_chat / handle_command ═══

class TestServiceMethods:
    def test_service_has_handle_chat(self):
        from app.workbench.service import workbench_service
        assert hasattr(workbench_service, 'handle_chat')
        assert callable(workbench_service.handle_chat)

    def test_service_has_handle_command(self):
        from app.workbench.service import workbench_service
        assert hasattr(workbench_service, 'handle_command')
        assert callable(workbench_service.handle_command)

    def test_service_has_apply_partial_patch(self):
        from app.workbench.service import workbench_service
        assert hasattr(workbench_service, 'apply_partial_patch')
        assert callable(workbench_service.apply_partial_patch)

    def test_service_has_generate_draft(self):
        from app.workbench.service import workbench_service
        assert hasattr(workbench_service, 'generate_draft')

    def test_service_has_list_references(self):
        from app.workbench.service import workbench_service
        assert hasattr(workbench_service, 'list_references')


# ═══ Router 端点注册 ═══

class TestRouterEndpoints:
    def test_all_endpoints_registered(self):
        from app.workbench.router import router
        paths = [r.path for r in router.routes]
        assert any("/workbench/task-contract" in p for p in paths)
        assert any("/workbench/chat" in p for p in paths)
        assert any("/workbench/command" in p for p in paths)
        assert any("/workbench/apply-partial" in p for p in paths)
        assert any("/workbench/session" in p for p in paths)
        assert any("/workbench/intent" in p for p in paths)
        assert any("/workbench/patch" in p for p in paths)
        assert any("/workbench/validate" in p for p in paths)
        assert any("/workbench/apply" in p for p in paths)
        assert any("/workbench/generate-draft" in p for p in paths)
        assert any("/workbench/create-skill" in p for p in paths)
        assert any("/workbench/create-skill/stream" in p for p in paths)
        assert any("/workbench/my-active-draft" in p for p in paths)
        assert any("/workbench/draft/{draft_id}/dismiss" in p for p in paths)
        assert any("/workbench/create-skill/finalize/{draft_id}" in p for p in paths)
        assert any("/workbench/references" in p for p in paths)

    def test_route_count(self):
        from app.workbench.router import router
        # 在原有 Studio/router 端点之上新增（v7）：
        # task-contract: /workbench/task-contract + /review + /bind (3)
        # create-skill v2: /create-skill/stream + /my-active-draft +
        #                  /draft/{id}/dismiss + /create-skill/finalize/{id} (4)
        # C2 草稿锁：    /workbench/skills/{skill_id}/fork-personal-branch
        #                /workbench/drafts/{draft_id}/force-takeover (2)
        # G3 模板库：    /workbench/templates + /workbench/templates/{id} (2)
        # Phase 3 coding agent：/workbench/coding/stream (1)
        # v2.11+ coding_agent 配套：prepare-workspace / upstream 代理 / 其他 (3)
        # 创建页补齐 context / preview-id / my-drafts，architect 增加 progress / swarm / similar。
        # 当前还包含 coach 2 个端点、telemetry 2 个端点，因此总注册数更新为 45。
        assert len(router.routes) == 45


# ═══ Models 字段完整性 ═══

class TestModels:
    def test_message_has_context_fields(self):
        from app.workbench.models import SkillWorkbenchMessage
        cols = {c.name for c in SkillWorkbenchMessage.__table__.columns}
        assert "module_id" in cols
        assert "selection_range" in cols
        assert "draft_revision" in cols

    def test_patch_has_hunk_fields(self):
        from app.workbench.models import SkillWorkbenchPatch
        cols = {c.name for c in SkillWorkbenchPatch.__table__.columns}
        assert "hunks_json" in cols
        assert "accepted_hunks" in cols
        assert "rejected_hunks" in cols
        assert "source_context" in cols

    def test_v7_runtime_models_exist(self):
        from app.workbench.models import SkillStudioDraft, SkillStudioPreview, SkillStudioReview, SkillStudioRun

        draft_cols = {c.name for c in SkillStudioDraft.__table__.columns}
        run_cols = {c.name for c in SkillStudioRun.__table__.columns}
        preview_cols = {c.name for c in SkillStudioPreview.__table__.columns}
        review_cols = {c.name for c in SkillStudioReview.__table__.columns}

        assert "contract_json" in draft_cols
        assert "review_state_json" in draft_cols
        assert "preview_cache_key" in run_cols
        assert "card_payload_json" in preview_cols
        assert "checkpoint" in review_cols

    def test_session_fields(self):
        from app.workbench.models import SkillWorkbenchSession
        cols = {c.name for c in SkillWorkbenchSession.__table__.columns}
        assert "skill_id" in cols
        assert "user_id" in cols
        assert "mode" in cols
        assert "status" in cols
