from types import SimpleNamespace
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import select


def _admin():
    return SimpleNamespace(id="admin", role="admin", department="AI", can_view_all=True)


def test_list_direct_capabilities_filters_by_department(monkeypatch):
    from app.hall import direct_capability_service

    monkeypatch.setattr(
        direct_capability_service,
        "_iter_direct_capabilities",
        lambda: [
            {
                "id": "ops-report",
                "display_name": "运营报表",
                "description": "生成运营报表",
                "category": "报表生成",
                "department": "运营",
                "status": "active",
                "visibility": "company",
            },
            {
                "id": "cs-insight",
                "display_name": "客服洞察",
                "description": "客服分析",
                "category": "客服洞察",
                "department": "客服",
                "status": "active",
                "visibility": "company",
            },
            {
                "id": "ops-risk",
                "display_name": "运营风险",
                "description": "运营风险检查",
                "category": "风险与合规",
                "department": "运营",
                "status": "active",
                "visibility": "company",
            },
        ],
    )

    listed = direct_capability_service.list_direct_capabilities(
        current_user=_admin(),
        department="运营",
        page_size=20,
    )

    assert listed["total"] == 2
    assert [item["id"] for item in listed["items"]] == ["ops-report", "ops-risk"]
    assert listed["departments"] == [
        {"name": "运营", "count": 2},
        {"name": "客服", "count": 1},
    ]


@pytest.mark.asyncio
async def test_run_direct_capability_streams_runner_logs(client, tmp_path, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service

    skill_dir = tmp_path / "stream-cap"
    script_dir = skill_dir / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "main.py").write_text(
        "\n".join([
            "import json",
            "import sys",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "print('progress token=' + str(payload.get('api_key')), file=sys.stderr, flush=True)",
            "print(json.dumps({'status': 'completed', 'value': payload.get('value')}), flush=True)",
        ]),
        encoding="utf-8",
    )

    def fake_resolve(capability_id):
        assert capability_id == "stream-cap"
        return skill_dir, {"runtime": {"timeout_seconds": 5}}, {
            "id": "stream-cap",
            "input_schema": {"type": "object", "properties": {}},
        }

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    events = []

    async def on_event(event):
        events.append(event)

    async with async_session_factory() as session:
        response = await direct_capability_service.run_direct_capability(
            "stream-cap",
            {"params": {"value": 7, "api_key": "secret-token"}},
            db=session,
            user_id="admin",
            current_user=_admin(),
            on_event=on_event,
        )

    assert response["result"]["status"] == "completed"
    assert response["result"]["value"] == 7
    assert any(event["type"] == "process_started" for event in events)
    assert any(event["type"] == "process_exit" and event["exit_code"] == 0 for event in events)
    assert any(
        event["type"] == "log"
        and event["stream"] == "stderr"
        and "[redacted]" in event["text"]
        and "secret-token" not in event["text"]
        for event in events
    )


@pytest.mark.asyncio
async def test_gpt_imagegen_run_forces_supported_model(client, tmp_path, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service

    skill_dir = tmp_path / "gpt-imagegen"
    script_dir = skill_dir / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "main.py").write_text(
        "\n".join([
            "import json",
            "import sys",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "print(json.dumps({'status': 'completed', 'model': payload.get('model')}), flush=True)",
        ]),
        encoding="utf-8",
    )

    def fake_resolve(capability_id):
        assert capability_id == "gpt-imagegen"
        return skill_dir, {"runtime": {"timeout_seconds": 5}}, {
            "id": "gpt-imagegen",
            "input_schema": {"type": "object", "properties": {}},
        }

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)

    async with async_session_factory() as session:
        response = await direct_capability_service.run_direct_capability(
            "gpt-imagegen",
            {"params": {"prompt": "白底商品图", "model": "gpt-image-1"}},
            db=session,
            user_id="admin",
            current_user=_admin(),
        )

    assert response["result"]["status"] == "completed"
    assert response["result"]["model"] == "gpt-image-2"


@pytest.mark.parametrize(
    ("size", "resolution", "expected"),
    [
        ("1:1", "1K", "1K"),
        ("16:9", "4K", "4K"),
        ("4:5", "2K", "2K"),
        ("16:9", "1K", "2K"),
        ("1:1", "4K", "2K"),
        ("4:5", "4K", "2K"),
    ],
)
def test_gpt_imagegen_normalizes_unsupported_resolution_pairs(size, resolution, expected):
    from app.hall import direct_capability_service

    normalized = direct_capability_service._normalize_direct_capability_payload(
        "gpt-imagegen",
        {"prompt": "商品图", "size": size, "resolution": resolution},
    )

    assert normalized["resolution"] == expected
    assert normalized["model"] == "gpt-image-2"


@pytest.mark.asyncio
async def test_gpt_imagegen_falls_back_to_vip_model_when_primary_unavailable(client, tmp_path, monkeypatch):
    from app.common.models import SystemConfig
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityImageHistory

    skill_dir = tmp_path / "gpt-imagegen"
    script_dir = skill_dir / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "main.py").write_text(
        "\n".join([
            "import json",
            "import sys",
            "print('HTTP 503 upstream unavailable', file=sys.stderr, flush=True)",
            "print(json.dumps({'status': 'failed', 'error': 'HTTP 503 upstream unavailable'}), flush=True)",
            "raise SystemExit(1)",
        ]),
        encoding="utf-8",
    )

    def fake_resolve(capability_id):
        assert capability_id == "gpt-imagegen"
        return skill_dir, {"runtime": {"timeout_seconds": 5}}, {
            "id": "gpt-imagegen",
            "input_schema": {"type": "object", "properties": {}},
        }

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"id":"vip-task-1","status":"completed","data":[{"url":"https://img.example.com/fallback.png"}]}'

    def fake_urlopen(request, timeout=None):
        body = request.data.decode("utf-8") if request.data else ""
        calls.append({
            "url": request.full_url,
            "method": request.get_method(),
            "body": body,
            "authorization": request.headers.get("Authorization"),
        })
        return FakeResponse()

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    monkeypatch.setattr(direct_capability_service.urllib_request, "urlopen", fake_urlopen)

    async with async_session_factory() as session:
        session.add_all([
            SystemConfig(key="ai.image_fallback.toapis.enabled", value=True, updated_by="test"),
            SystemConfig(key="ai.image_fallback.toapis.api_base", value="https://toapis.com/v1", updated_by="test"),
            SystemConfig(key="ai.image_fallback.toapis.api_key", value="sk-vip-secret", updated_by="test"),
            SystemConfig(key="ai.image_fallback.toapis.model", value="gpt-image-2-vip", updated_by="test"),
        ])
        await session.commit()

    async with async_session_factory() as session:
        response = await direct_capability_service.run_direct_capability(
            "gpt-imagegen",
            {"params": {"prompt": "白底商品图", "model": "gpt-image-1", "download": False}},
            db=session,
            user_id="admin",
            current_user=_admin(),
        )
        await session.commit()

    assert response["result"]["status"] == "completed"
    assert response["result"]["model"] == "gpt-image-2-vip"
    assert response["result"]["fallback"] is True
    assert response["result"]["image_urls"] == ["https://img.example.com/fallback.png"]
    assert calls
    assert calls[0]["method"] == "POST"
    assert calls[0]["authorization"] == "Bearer sk-vip-secret"
    assert '"model": "gpt-image-2-vip"' in calls[0]["body"]

    async with async_session_factory() as session:
        history = (await session.execute(select(DirectCapabilityImageHistory))).scalar_one()
        assert history.capability_id == "gpt-imagegen"
        assert history.image_url == "https://img.example.com/fallback.png"
        assert history.task_id == "vip-task-1"


@pytest.mark.asyncio
async def test_gpt_imagegen_fallback_reads_legacy_config_when_system_config_absent(client, tmp_path, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service

    skill_dir = tmp_path / "runtime-gpt-imagegen"
    script_dir = skill_dir / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "main.py").write_text(
        "\n".join([
            "import json",
            "import sys",
            "print('HTTP 503 circuit breaker', file=sys.stderr, flush=True)",
            "print(json.dumps({'status': 'failed', 'error': 'HTTP 503 circuit breaker'}), flush=True)",
            "raise SystemExit(1)",
        ]),
        encoding="utf-8",
    )
    repo_root = tmp_path / "skills-repo"
    legacy_config_dir = repo_root / "gpt-imagegen"
    legacy_config_dir.mkdir(parents=True)
    (legacy_config_dir / "config.json").write_text(
        '{"baseUrl":"https://toapis.com","apiKey":"sk-legacy-vip"}',
        encoding="utf-8",
    )

    def fake_resolve(capability_id):
        assert capability_id == "gpt-imagegen"
        return skill_dir, {"runtime": {"timeout_seconds": 5}}, {
            "id": "gpt-imagegen",
            "input_schema": {"type": "object", "properties": {}},
        }

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"id":"legacy-vip-task","status":"completed","data":[{"url":"https://img.example.com/legacy.png"}]}'

    def fake_urlopen(request, timeout=None):
        calls.append({
            "url": request.full_url,
            "body": request.data.decode("utf-8") if request.data else "",
            "authorization": request.headers.get("Authorization"),
        })
        return FakeResponse()

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    monkeypatch.setattr(direct_capability_service, "_skills_repo_candidates", lambda: [repo_root])
    monkeypatch.setattr(direct_capability_service.urllib_request, "urlopen", fake_urlopen)

    async with async_session_factory() as session:
        response = await direct_capability_service.run_direct_capability(
            "gpt-imagegen",
            {"params": {"prompt": "白底商品图", "download": False}},
            db=session,
            user_id="admin",
            current_user=_admin(),
        )

    assert response["result"]["fallback"] is True
    assert response["result"]["model"] == "gpt-image-2-vip"
    assert response["result"]["image_urls"] == ["https://img.example.com/legacy.png"]
    assert calls[0]["authorization"] == "Bearer sk-legacy-vip"
    assert '"model": "gpt-image-2-vip"' in calls[0]["body"]


@pytest.mark.asyncio
async def test_gpt_imagegen_fallback_creates_new_task_for_failed_upstream_task(client, tmp_path, monkeypatch):
    from app.common.models import SystemConfig
    from app.database import async_session_factory
    from app.hall import direct_capability_service

    skill_dir = tmp_path / "gpt-imagegen"
    script_dir = skill_dir / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "main.py").write_text(
        "\n".join([
            "import json",
            "import sys",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "assert payload.get('task_id') == 'failed-task-1'",
            "print(json.dumps({'status': 'failed', 'task_id': payload.get('task_id'), 'error': {'code': 'generation_failed', 'message': '生成失败：任务处理失败'}}), flush=True)",
            "raise SystemExit(1)",
        ]),
        encoding="utf-8",
    )

    def fake_resolve(capability_id):
        assert capability_id == "gpt-imagegen"
        return skill_dir, {"runtime": {"timeout_seconds": 5}}, {
            "id": "gpt-imagegen",
            "input_schema": {"type": "object", "properties": {}},
        }

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"id":"fallback-task-1","status":"completed","data":[{"url":"https://img.example.com/recovered.png"}]}'

    def fake_urlopen(request, timeout=None):
        calls.append({
            "url": request.full_url,
            "method": request.get_method(),
            "body": request.data.decode("utf-8") if request.data else "",
        })
        return FakeResponse()

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    monkeypatch.setattr(direct_capability_service.urllib_request, "urlopen", fake_urlopen)

    async with async_session_factory() as session:
        session.add_all([
            SystemConfig(key="ai.image_fallback.toapis.enabled", value=True, updated_by="test"),
            SystemConfig(key="ai.image_fallback.toapis.api_key", value="sk-vip-secret", updated_by="test"),
            SystemConfig(key="ai.image_fallback.toapis.model", value="gpt-image-2-vip", updated_by="test"),
        ])
        await session.commit()

    async with async_session_factory() as session:
        response = await direct_capability_service.run_direct_capability(
            "gpt-imagegen",
            {"params": {"task_id": "failed-task-1", "prompt": "白底商品图", "download": False}},
            db=session,
            user_id="admin",
            current_user=_admin(),
        )

    assert response["result"]["status"] == "completed"
    assert response["result"]["fallback"] is True
    assert response["result"]["fallback_replaced_task_id"] == "failed-task-1"
    assert response["result"]["task_id"] == "fallback-task-1"
    assert response["result"]["image_urls"] == ["https://img.example.com/recovered.png"]
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/images/generations")
    assert "failed-task-1" not in calls[0]["body"]
    assert '"prompt": "白底商品图"' in calls[0]["body"]


@pytest.mark.asyncio
async def test_gpt_imagegen_does_not_fallback_for_user_input_error(client, tmp_path, monkeypatch):
    from app.common.models import SystemConfig
    from app.database import async_session_factory
    from app.hall import direct_capability_service

    skill_dir = tmp_path / "gpt-imagegen"
    script_dir = skill_dir / "scripts"
    script_dir.mkdir(parents=True)
    (script_dir / "main.py").write_text(
        "\n".join([
            "import json",
            "print(json.dumps({'status': 'failed', 'error': 'prompt is required unless task_id is provided'}), flush=True)",
            "raise SystemExit(1)",
        ]),
        encoding="utf-8",
    )

    def fake_resolve(capability_id):
        assert capability_id == "gpt-imagegen"
        return skill_dir, {"runtime": {"timeout_seconds": 5}}, {
            "id": "gpt-imagegen",
            "input_schema": {"type": "object", "properties": {}},
        }

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("fallback should not be called for input validation errors")

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    monkeypatch.setattr(direct_capability_service.urllib_request, "urlopen", fail_urlopen)

    async with async_session_factory() as session:
        session.add_all([
            SystemConfig(key="ai.image_fallback.toapis.enabled", value=True, updated_by="test"),
            SystemConfig(key="ai.image_fallback.toapis.api_key", value="sk-vip-secret", updated_by="test"),
        ])
        await session.commit()

    async with async_session_factory() as session:
        response = await direct_capability_service.run_direct_capability(
            "gpt-imagegen",
            {"params": {"model": "gpt-image-1"}},
            db=session,
            user_id="admin",
            current_user=_admin(),
        )

    assert response["result"]["status"] == "failed"
    assert response["result"].get("fallback") is not True


@pytest.mark.asyncio
async def test_direct_capability_task_create_and_poll_saves_history(client, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.common.time_utils import now_bjt
    from app.hall.models import DirectCapabilityImageHistory, DirectCapabilityTask

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        params = (body or {}).get("params", {})
        if params.get("task_id"):
            response = {
                "capability_id": capability_id,
                "result": {
                    "status": "completed",
                    "task_id": params["task_id"],
                    "image_urls": ["https://img.example.com/result.png"],
                },
            }
            await direct_capability_service.save_direct_capability_image_history(
                db,
                user_id=user_id,
                capability_id=capability_id,
                payload=params,
                result=response,
            )
            return response
        assert params["wait"] is False
        return {
            "capability_id": capability_id,
            "result": {"status": "queued", "task_id": "upstream-1", "progress": 0},
        }

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    async with async_session_factory() as session:
        created = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": {
                "workspace_id": "ws-main",
                "workspace_name": "主图 A",
                "prompt": "白底商品图",
                "size": "1:1",
                "resolution": "1K",
                "n": 1,
            }},
            user_id="admin",
            current_user=_admin(),
        )
        assert created["status"] == "queued"
        assert created["upstream_task_id"] is None
        task_row = await session.get(DirectCapabilityTask, created["id"])

        listed = await direct_capability_service.list_direct_capability_tasks(
            session,
            "gpt-imagegen",
            user_id="admin",
            current_user=_admin(),
            advance=True,
        )
        row = listed["items"][0]
        assert row["status"] == "in_progress"
        task_row.next_poll_at = now_bjt()

        listed = await direct_capability_service.list_direct_capability_tasks(
            session,
            "gpt-imagegen",
            user_id="admin",
            current_user=_admin(),
            advance=True,
        )
        row = listed["items"][0]
        assert row["status"] == "completed"
        assert row["image_urls"] == ["https://img.example.com/result.png"]

        history = (await session.execute(select(DirectCapabilityImageHistory))).scalar_one()
        assert history.prompt == "白底商品图"
        assert history.extra["workspace_id"] == "ws-main"
        assert history.extra["workspace_name"] == "主图 A"


@pytest.mark.asyncio
async def test_gpt_imagegen_task_normalizes_legacy_model_before_submit(client, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    submitted_params = []

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        submitted_params.append((body or {}).get("params", {}))
        return {
            "capability_id": capability_id,
            "result": {"status": "queued", "task_id": "upstream-1", "progress": 0},
        }

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    async with async_session_factory() as session:
        created = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": {
                "workspace_id": "ws-main",
                "workspace_name": "主图 A",
                "prompt": "白底商品图",
                "model": "gpt-image-1",
            }},
            user_id="admin",
            current_user=_admin(),
        )
        task_row = await session.get(DirectCapabilityTask, created["id"])
        assert task_row.params["model"] == "gpt-image-2"

        await direct_capability_service.advance_due_direct_capability_tasks(
            session,
            capability_ids=("gpt-imagegen",),
        )

    assert submitted_params[0]["model"] == "gpt-image-2"


@pytest.mark.asyncio
async def test_direct_capability_task_duplicate_submit_reuses_active_task(client, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    counter = {"value": 0}

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        counter["value"] += 1
        return {
            "capability_id": capability_id,
            "result": {"status": "queued", "task_id": f"upstream-{counter['value']}"},
        }

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    payload = {
        "workspace_id": "ws-main",
        "workspace_name": "主图 A",
        "prompt": "白底商品图",
        "size": "1:1",
        "resolution": "1K",
        "n": 1,
        "reference_images": [],
    }
    async with async_session_factory() as session:
        first = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": payload},
            user_id="admin",
            current_user=_admin(),
        )
        second = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": {**payload, "download": True}},
            user_id="admin",
            current_user=_admin(),
        )
        rows = (await session.execute(select(DirectCapabilityTask))).scalars().all()

    assert second["id"] == first["id"]
    assert second["upstream_task_id"] is None
    assert counter["value"] == 0
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_direct_capability_task_duplicate_submit_reuses_recent_completed_task(client, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    counter = {"value": 0}

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        counter["value"] += 1
        return {
            "capability_id": capability_id,
            "result": {
                "status": "completed",
                "task_id": f"upstream-{counter['value']}",
                "image_urls": [f"https://img.example.com/{counter['value']}.png"],
            },
        }

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    payload = {
        "workspace_id": "ws-main",
        "workspace_name": "主图 A",
        "prompt": "白底商品图",
        "size": "1:1",
        "resolution": "1K",
        "n": 1,
    }
    async with async_session_factory() as session:
        first = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": payload},
            user_id="admin",
            current_user=_admin(),
        )
        row = await session.get(DirectCapabilityTask, first["id"])
        assert row is not None
        await direct_capability_service.advance_due_direct_capability_tasks(session, capability_ids=("gpt-imagegen",))
        row.completed_at = now_bjt()
        row.updated_at = row.completed_at
        await session.flush()

        second = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": {**payload, "download": True}},
            user_id="admin",
            current_user=_admin(),
        )
        rows = (await session.execute(select(DirectCapabilityTask))).scalars().all()

    assert second["id"] == first["id"]
    assert second["upstream_task_id"] == "upstream-1"
    assert counter["value"] == 1
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_direct_capability_task_retry_force_creates_new_task(client, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    counter = {"value": 0}

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        counter["value"] += 1
        return {
            "capability_id": capability_id,
            "result": {"status": "completed", "task_id": f"upstream-{counter['value']}"},
        }

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    payload = {
        "workspace_id": "ws-main",
        "workspace_name": "主图 A",
        "prompt": "白底商品图",
        "size": "1:1",
        "resolution": "1K",
        "n": 1,
    }
    async with async_session_factory() as session:
        first = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": payload},
            user_id="admin",
            current_user=_admin(),
        )
        retry = await direct_capability_service.retry_direct_capability_task(
            session,
            "gpt-imagegen",
            first["id"],
            user_id="admin",
            current_user=_admin(),
        )
        await direct_capability_service.advance_due_direct_capability_tasks(session, capability_ids=("gpt-imagegen",))
        rows = (await session.execute(select(DirectCapabilityTask))).scalars().all()

    assert retry["id"] != first["id"]
    assert {row.upstream_task_id for row in rows} == {"upstream-1", "upstream-2"}
    assert counter["value"] == 2
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_direct_capability_task_runner_conflict_waits_for_next_poll(client, monkeypatch):
    from fastapi import HTTPException
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        raise HTTPException(status_code=409, detail="相同生成请求正在运行，请勿重复提交")

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    async with async_session_factory() as session:
        created = await direct_capability_service.create_direct_capability_task(
            session,
            "gpt-imagegen",
            {"params": {
                "workspace_id": "ws-main",
                "workspace_name": "主图 A",
                "prompt": "白底商品图",
            }},
            user_id="admin",
            current_user=_admin(),
        )
        row = await session.get(DirectCapabilityTask, created["id"])
        await direct_capability_service.advance_due_direct_capability_tasks(session, capability_ids=("gpt-imagegen",))
        await session.refresh(row)

    assert created["status"] == "queued"
    assert row.status == "in_progress"
    assert row.result["status"] == "in_progress"
    assert row.error is None
    assert row.completed_at is None
    assert row.next_poll_at is not None


@pytest.mark.asyncio
async def test_direct_capability_task_active_limit_queues_fourth(client, monkeypatch):
    from app.database import async_session_factory
    from app.hall import direct_capability_service

    counter = {"value": 0}

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        counter["value"] += 1
        return {
            "capability_id": capability_id,
            "result": {"status": "queued", "task_id": f"upstream-{counter['value']}"},
        }

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    async with async_session_factory() as session:
        statuses = []
        for idx in range(4):
            created = await direct_capability_service.create_direct_capability_task(
                session,
                "gpt-imagegen",
                {"params": {
                    "workspace_id": f"ws-{idx}",
                    "workspace_name": f"方案 {idx}",
                    "prompt": f"prompt {idx}",
                }},
                user_id="admin",
                current_user=_admin(),
            )
            statuses.append(created["status"])

    assert statuses == ["queued", "queued", "queued", "queued"]
    assert counter["value"] == 0


@pytest.mark.asyncio
async def test_direct_capability_background_advancer_releases_stale_and_starts_queue(client, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    now = now_bjt()
    stale_started = now - timedelta(seconds=direct_capability_service._TASK_STALE_TIMEOUT_SECONDS + 30)
    counter = {"value": 0}

    async def fake_run(capability_id, body, *, db=None, user_id=None, current_user=None):
        counter["value"] += 1
        return {
            "capability_id": capability_id,
            "result": {"status": "queued", "task_id": f"upstream-new-{counter['value']}"},
        }

    monkeypatch.setattr(direct_capability_service, "run_direct_capability", fake_run)

    stale_rows = [
        DirectCapabilityTask(
            id=str(uuid.uuid4()),
            user_id="admin",
            capability_id="gpt-imagegen",
            status="in_progress",
            prompt=f"stale {idx}",
            params={"prompt": f"stale {idx}", "wait": False, "download": False},
            result={"result": {"status": "in_progress", "progress": 10}},
            upstream_task_id=f"upstream-stale-{idx}",
            started_at=stale_started,
            created_at=stale_started,
            updated_at=stale_started,
            next_poll_at=now - timedelta(seconds=1),
        )
        for idx in range(3)
    ]
    queued = DirectCapabilityTask(
        id=str(uuid.uuid4()),
        user_id="admin",
        capability_id="gpt-imagegen",
        status="queued",
        prompt="new queued",
        params={"prompt": "new queued", "wait": False, "download": False},
        created_at=now,
        updated_at=now,
    )

    async with async_session_factory() as session:
        session.add_all([*stale_rows, queued])
        result = await direct_capability_service.advance_due_direct_capability_tasks(
            session,
            capability_ids=("gpt-imagegen",),
            limit=10,
        )
        queued_row = await session.get(DirectCapabilityTask, queued.id)
        stale_after = [
            await session.get(DirectCapabilityTask, stale.id)
            for stale in stale_rows
        ]

    assert result["failed_stale"] == 3
    assert all(row.status == "failed" for row in stale_after)
    assert queued_row.status == "in_progress"
    assert queued_row.upstream_task_id == "upstream-new-1"


@pytest.mark.asyncio
async def test_list_direct_capability_tasks_returns_real_stats(client, tmp_path, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    now = now_bjt()
    capability_id = "stats-cap"
    skill_dir = tmp_path / capability_id
    skill_dir.mkdir()

    def fake_resolve(target_id):
        assert target_id == capability_id
        return skill_dir, {}, {"id": capability_id, "input_schema": {"type": "object", "properties": {}}}

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    rows = [
        DirectCapabilityTask(
            id=str(uuid.uuid4()),
            user_id="admin",
            capability_id=capability_id,
            status="completed",
            params={},
            result={"status": "completed"},
            started_at=now - timedelta(seconds=14),
            completed_at=now - timedelta(seconds=10),
            created_at=now - timedelta(minutes=3),
            updated_at=now,
        ),
        DirectCapabilityTask(
            id=str(uuid.uuid4()),
            user_id="admin",
            capability_id=capability_id,
            status="failed",
            params={},
            result={"status": "failed"},
            error="boom",
            started_at=now - timedelta(seconds=9),
            completed_at=now - timedelta(seconds=1),
            created_at=now - timedelta(minutes=2),
            updated_at=now,
        ),
        DirectCapabilityTask(
            id=str(uuid.uuid4()),
            user_id="admin",
            capability_id=capability_id,
            status="queued",
            params={},
            created_at=now - timedelta(minutes=1),
            updated_at=now,
        ),
    ]

    async with async_session_factory() as session:
        session.add_all(rows)
        listed = await direct_capability_service.list_direct_capability_tasks(
            session,
            capability_id,
            user_id="admin",
            current_user=_admin(),
            advance=False,
        )

    stats = listed["stats"]
    assert stats["total"] == 3
    assert stats["success_count"] == 1
    assert stats["failed_count"] == 1
    assert stats["terminal_count"] == 2
    assert stats["success_rate"] == 50.0
    assert stats["avg_duration_seconds"] == 6.0
    assert stats["last_run_at"] is not None


@pytest.mark.asyncio
async def test_list_direct_capability_tasks_completes_running_task_with_saved_image_result(client, tmp_path, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    capability_id = "image-status-cap"
    skill_dir = tmp_path / capability_id
    skill_dir.mkdir()

    def fake_resolve(target_id):
        assert target_id == capability_id
        return skill_dir, {}, {"id": capability_id, "input_schema": {"type": "object", "properties": {}}}

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    now = now_bjt()
    task_id = str(uuid.uuid4())
    async with async_session_factory() as session:
        session.add(DirectCapabilityTask(
            id=task_id,
            user_id="admin",
            capability_id=capability_id,
            workspace_id="ws-main",
            workspace_name="工作区1",
            status="in_progress",
            prompt="白底商品图",
            upstream_task_id="upstream-image",
            params={"prompt": "白底商品图", "wait": False, "download": False},
            result={
                "capability_id": capability_id,
                "result": {
                    "status": "in_progress",
                    "task_id": "upstream-image",
                    "image_urls": ["https://img.example.com/done.png"],
                },
            },
            started_at=now,
            created_at=now,
            updated_at=now,
        ))
        listed = await direct_capability_service.list_direct_capability_tasks(
            session,
            capability_id,
            user_id="admin",
            current_user=_admin(),
            advance=True,
        )

    row = listed["items"][0]
    assert row["status"] == "completed"
    assert row["error"] is None
    assert row["image_urls"] == ["https://img.example.com/done.png"]
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_list_direct_capability_tasks_completes_running_task_when_history_has_upstream_image(client, tmp_path, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityImageHistory, DirectCapabilityTask

    capability_id = "image-history-status-cap"
    skill_dir = tmp_path / capability_id
    skill_dir.mkdir()

    def fake_resolve(target_id):
        assert target_id == capability_id
        return skill_dir, {}, {"id": capability_id, "input_schema": {"type": "object", "properties": {}}}

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    now = now_bjt()
    async with async_session_factory() as session:
        session.add_all([
            DirectCapabilityTask(
                id=str(uuid.uuid4()),
                user_id="admin",
                capability_id=capability_id,
                workspace_id="ws-main",
                workspace_name="工作区1",
                status="in_progress",
                prompt="白底商品图",
                upstream_task_id="upstream-history",
                params={"prompt": "白底商品图", "wait": False, "download": False},
                result={
                    "capability_id": capability_id,
                    "result": {"status": "in_progress", "task_id": "upstream-history"},
                },
                started_at=now,
                created_at=now,
                updated_at=now,
            ),
            DirectCapabilityImageHistory(
                user_id="admin",
                capability_id=capability_id,
                image_url="https://img.example.com/history-done.png",
                name="history-done.png",
                prompt="白底商品图",
                task_id="upstream-history",
                source="generate",
                extra={"workspace_id": "ws-main", "workspace_name": "工作区1"},
                created_at=now,
            ),
        ])
        listed = await direct_capability_service.list_direct_capability_tasks(
            session,
            capability_id,
            user_id="admin",
            current_user=_admin(),
            advance=True,
        )

    row = listed["items"][0]
    assert row["status"] == "completed"
    assert row["error"] is None
    assert row["image_urls"] == ["https://img.example.com/history-done.png"]
    assert row["result"]["result"]["image_urls"] == ["https://img.example.com/history-done.png"]
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_get_direct_capability_task_accepts_upstream_task_id(client, tmp_path, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityTask

    capability_id = "image-upstream-get-cap"
    skill_dir = tmp_path / capability_id
    skill_dir.mkdir()

    def fake_resolve(target_id):
        assert target_id == capability_id
        return skill_dir, {}, {"id": capability_id, "input_schema": {"type": "object", "properties": {}}}

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    now = now_bjt()
    async with async_session_factory() as session:
        session.add(DirectCapabilityTask(
            id=str(uuid.uuid4()),
            user_id="admin",
            capability_id=capability_id,
            workspace_id="ws-main",
            workspace_name="工作区1",
            status="completed",
            prompt="白底商品图",
            upstream_task_id="tsk_img_upstream_get",
            params={"prompt": "白底商品图", "wait": False, "download": False},
            result={
                "capability_id": capability_id,
                "result": {
                    "status": "completed",
                    "task_id": "tsk_img_upstream_get",
                    "image_urls": ["https://img.example.com/upstream.png"],
                },
            },
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        ))
        task = await direct_capability_service.get_direct_capability_task(
            session,
            capability_id,
            "tsk_img_upstream_get",
            user_id="admin",
            current_user=_admin(),
            advance=False,
        )

    assert task["status"] == "completed"
    assert task["upstream_task_id"] == "tsk_img_upstream_get"
    assert task["image_urls"] == ["https://img.example.com/upstream.png"]


@pytest.mark.asyncio
async def test_list_direct_capability_artifacts_returns_current_user_history(client, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityImageHistory

    monkeypatch.setattr(
        direct_capability_service,
        "_iter_direct_capabilities",
        lambda: [
            {
                "id": "image-cap",
                "display_name": "图片生成",
                "status": "active",
                "visibility": "company",
            },
        ],
    )
    now = now_bjt()
    rows = [
        DirectCapabilityImageHistory(
            user_id="admin",
            capability_id="image-cap",
            image_url="https://img.example.com/1.png",
            name="图 1",
            prompt="白底商品图",
            source="task",
            created_at=now,
        ),
        DirectCapabilityImageHistory(
            user_id="other",
            capability_id="image-cap",
            image_url="https://img.example.com/2.png",
            name="图 2",
            prompt="其他用户图片",
            source="task",
            created_at=now,
        ),
    ]

    async with async_session_factory() as session:
        session.add_all(rows)
        listed = await direct_capability_service.list_direct_capability_artifacts(
            session,
            user_id="admin",
            current_user=_admin(),
            page=1,
            page_size=10,
        )

    assert listed["total"] == 1
    assert listed["items"][0]["image_url"] == "https://img.example.com/1.png"
    assert listed["items"][0]["capability_name"] == "图片生成"


@pytest.mark.asyncio
async def test_image_history_follows_verified_dingtalk_identity_reconciliation(client, tmp_path, monkeypatch):
    from app.common.audit import AuditLog
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityImageHistory, DirectCapabilityTask

    capability_id = "gpt-imagegen"
    skill_dir = tmp_path / capability_id
    skill_dir.mkdir()
    monkeypatch.setattr(
        direct_capability_service,
        "_resolve_capability",
        lambda _target_id: (
            skill_dir,
            {},
            {"id": capability_id, "status": "active", "visibility": "company", "input_schema": {}},
        ),
    )
    now = now_bjt()
    async with async_session_factory() as session:
        session.add_all([
            AuditLog(
                user_id="canonical-user",
                action="user.login",
                detail={
                    "identity_source": "union_id_verified",
                    "enterprise_identity_verified": True,
                    "reconciled_shadow_user_id": "dt-legacy-user",
                },
                created_at=now,
            ),
            DirectCapabilityImageHistory(
                user_id="dt-legacy-user",
                capability_id=capability_id,
                image_url="https://img.example.com/legacy.png",
                name="legacy.png",
                created_at=now,
            ),
            DirectCapabilityImageHistory(
                user_id="unrelated-user",
                capability_id=capability_id,
                image_url="https://img.example.com/unrelated.png",
                name="unrelated.png",
                created_at=now,
            ),
            DirectCapabilityTask(
                id="legacy-task",
                user_id="dt-legacy-user",
                capability_id=capability_id,
                status="completed",
                params={},
                result={"image_urls": ["https://img.example.com/legacy.png"]},
                created_at=now,
                updated_at=now,
            ),
        ])
        await session.flush()
        history = await direct_capability_service.list_direct_capability_image_history(
            session,
            user_id="canonical-user",
            capability_id=capability_id,
            page=1,
            page_size=20,
        )
        tasks = await direct_capability_service.list_direct_capability_tasks(
            session,
            capability_id,
            user_id="canonical-user",
            page=1,
            page_size=20,
            advance=False,
        )

    assert history["total"] == 1
    assert history["items"][0]["image_url"] == "https://img.example.com/legacy.png"
    assert tasks["total"] == 1
    assert tasks["items"][0]["id"] == "legacy-task"


@pytest.mark.asyncio
async def test_list_direct_capability_image_history_never_probes_remote_sizes(client, tmp_path, monkeypatch):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.hall import direct_capability_service
    from app.hall.models import DirectCapabilityImageHistory

    capability_id = "gpt-imagegen"
    skill_dir = tmp_path / capability_id
    skill_dir.mkdir()

    def fake_resolve(target_id):
        assert target_id == capability_id
        return skill_dir, {}, {"id": capability_id, "input_schema": {"type": "object", "properties": {}}}

    monkeypatch.setattr(direct_capability_service, "_resolve_capability", fake_resolve)
    monkeypatch.setattr(
        direct_capability_service,
        "_measure_image_url_size",
        lambda _url: (_ for _ in ()).throw(AssertionError("request path must not probe remote images")),
    )

    now = now_bjt()
    async with async_session_factory() as session:
        session.add_all([
            DirectCapabilityImageHistory(
                user_id="admin",
                capability_id=capability_id,
                image_url="https://img.example.com/generated-1.png",
                name="generated-1.png",
                byte_size=1024,
                prompt="主图",
                source="generate",
                created_at=now - timedelta(minutes=2),
            ),
            DirectCapabilityImageHistory(
                user_id="admin",
                capability_id=capability_id,
                image_url="https://img.example.com/generated-2.png",
                name="generated-2.png",
                prompt="主图",
                source="generate",
                created_at=now,
            ),
        ])
        listed = await direct_capability_service.list_direct_capability_image_history(
            session,
            user_id="admin",
            capability_id=capability_id,
            page=1,
            page_size=10,
            refresh_missing_sizes=True,
        )
        refreshed = (
            await session.execute(
                select(DirectCapabilityImageHistory).where(
                    DirectCapabilityImageHistory.image_url == "https://img.example.com/generated-2.png"
                )
            )
        ).scalar_one()

    assert listed["total"] == 2
    assert listed["stats"] == {
        "total_count": 2,
        "known_count": 1,
        "unknown_count": 1,
        "known_bytes": 1024,
    }
    assert listed["size_refresh"] == {
        "attempted": 0,
        "refreshed": 0,
        "failed": 0,
        "capped": False,
        "requested": True,
        "mode": "deferred",
    }
    assert listed["items"][0]["byte_size"] is None
    assert refreshed.byte_size is None
