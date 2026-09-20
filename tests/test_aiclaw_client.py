import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest


class DummyWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000, reason=''):
        return None


async def _wait_sent(ws: DummyWS, count: int = 1) -> list[dict]:
    for _ in range(100):
        if len(ws.sent) >= count:
            return ws.sent
        await asyncio.sleep(0.01)
    return ws.sent


def _chat_user(*, user_id='u-ec', role='aibp', department='EC', can_view_all=False):
    return SimpleNamespace(
        id=user_id,
        role=role,
        department=department,
        can_view_all=can_view_all,
        is_active=True,
        state='active',
    )


def _bridge_state_artifact_uri(instance_id: str, name: str = 'model.tar.gz') -> str:
    slug = hashlib.md5(instance_id.encode('utf-8')).hexdigest()[:8]
    return f'/home/test/.skillforge_bridge/{slug}/training_runs/job/output/{name}'


@pytest.mark.asyncio
async def test_bridge_unregister_ignores_stale_replaced_socket():
    from app.aiclaw.bridge_registry import bridge_registry

    ws1 = DummyWS()
    ws2 = DummyWS()
    await bridge_registry.register('inst-replace-1', ws1)
    current = await bridge_registry.register('inst-replace-1', ws2)

    try:
        removed_old = await bridge_registry.unregister('inst-replace-1', ws1)
        assert removed_old is False
        assert bridge_registry.get('inst-replace-1') is current

        removed_current = await bridge_registry.unregister('inst-replace-1', ws2)
        assert removed_current is True
        assert bridge_registry.get('inst-replace-1') is None
    finally:
        await bridge_registry.unregister('inst-replace-1')


@pytest.mark.asyncio
async def test_aiclaw_list_agents_roundtrip():
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient

    ws = DummyWS()
    conn = await bridge_registry.register('inst-1', ws)
    try:
        task = asyncio.create_task(AIClawClient('inst-1').list_agents())
        await asyncio.sleep(0)
        request = ws.sent[0]
        assert request['method'] == 'agents.list'

        await conn.handle_forward_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'payload': {'agents': [{'id': 'main'}]},
        })
        result = await task
        assert result == [{'id': 'main'}]
    finally:
        await bridge_registry.unregister('inst-1')


@pytest.mark.asyncio
async def test_aiclaw_training_inference_bridge_op_roundtrip():
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient

    ws = DummyWS()
    conn = await bridge_registry.register('inst-infer-1', ws)
    try:
        task = asyncio.create_task(AIClawClient('inst-infer-1').run_training_inference({'prompt': 'hello'}))
        await asyncio.sleep(0)
        request = ws.sent[0]
        assert request['type'] == 'bridge_op'
        assert request['op'] == 'training.inference'
        assert request['payload'] == {'prompt': 'hello'}

        await conn.handle_bridge_op_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'result': {'text': 'adapter ok'},
        })
        result = await task
        assert result == {'text': 'adapter ok'}
    finally:
        await bridge_registry.unregister('inst-infer-1')


def test_chat_inference_text_cleans_reasoning_and_role_markers():
    from app.aiclaw.router import _chat_inference_text

    text = _chat_inference_text({
        'text': '<think>内部推理</think>\n\n第一版脚本\nassistant\n<think></think>\n第二版脚本',
    })

    assert text == '第一版脚本'


def test_chat_inference_text_strips_thinking_process_leak():
    from app.aiclaw.router import _chat_inference_text

    text = _chat_inference_text({
        'text': '<think>\nThinking Process:\n1. 分析用户问题\n2. 规划回答',
    })

    assert text == ''


def test_chat_inference_text_strips_trailing_mistral_signature_noise():
    from app.aiclaw.router import _chat_inference_text

    text = _chat_inference_text({
        'text': '防烫杯身，轻装上课。点击左下角链接。#学生党 #平价好物Mistral',
    })

    assert text == '防烫杯身，轻装上课。点击左下角链接。#学生党 #平价好物'


def test_chat_inference_text_keeps_regular_mistral_mentions():
    from app.aiclaw.router import _chat_inference_text

    text = _chat_inference_text({
        'text': '推荐模型：Mistral',
    })

    assert text == '推荐模型：Mistral'


def test_chat_inference_text_strips_prompt_template_leak():
    from app.aiclaw.router import _chat_inference_text

    text = _chat_inference_text({
        'text': (
            '直播话术已经完成。\n'
            'system\n'
            'You are the conversation interface for a deployed and trained model named "SkillForge".\n'
            '<|model_context|>\n'
            'Deployment ID: deploy-1'
        ),
    })

    assert text == '直播话术已经完成。'


def test_chat_inference_text_strips_short_tail_fragment_noise():
    from app.aiclaw.router import _chat_inference_text

    text = _chat_inference_text({
        'text': '整改建议清单已经可以直接给运营执行。ng',
    })

    assert text == '整改建议清单已经可以直接给运营执行。'


@pytest.mark.asyncio
async def test_aiclaw_chat_send_streams_chunks():
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient

    ws = DummyWS()
    conn = await bridge_registry.register('inst-2', ws)
    chunks = []

    async def consume():
        async for chunk in AIClawClient('inst-2').chat_send(
            'main',
            'hello',
            model_context={'model_deployment_id': 'deploy-1', 'model_family': 'ranker'},
        ):
            chunks.append(chunk)

    try:
        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        request = ws.sent[0]
        assert request['method'] == 'chat.send'
        assert request['params']['modelContext'] == {'model_deployment_id': 'deploy-1', 'model_family': 'ranker'}

        await conn.handle_forward_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'payload': {},
        })
        await asyncio.sleep(0)
        await conn.handle_forward_event({
            'epoch': conn.epoch,
            'event': 'agent',
            'payload': {'runId': chunks[0]['runId'] if chunks else request['params']['idempotencyKey'] if 'params' in request else None, 'data': {'text': '你好'}},
        })
        await conn.handle_forward_event({
            'epoch': conn.epoch,
            'event': 'chat',
            'payload': {
                'runId': chunks[0]['runId'] if chunks else request['params']['idempotencyKey'] if 'params' in request else None,
                'state': 'final',
                'message': {'content': [{'type': 'text', 'text': '完成'}]},
            },
        })
        await task

        assert chunks[0]['state'] == 'started'
        assert any(item.get('delta') == '你好' for item in chunks)
        assert chunks[-1]['state'] == 'final'
        assert chunks[-1]['text'] == '完成'
    finally:
        await bridge_registry.unregister('inst-2')


@pytest.mark.asyncio
async def test_aiclaw_instances_include_agent_purpose_analysis_and_active_training_jobs(client):
    import app.database as db_mod
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-analysis-1',
            name='EC 分析 Agent',
            department='EC',
            gateway_url='ws://127.0.0.1:18789',
            reload_hook_url='http://127.0.0.1:9000/reload',
            reload_token='',
            is_active=True,
            agent_purpose='analysis',
            bridge_capabilities_json=json.dumps({
                'ops': ['intelligence.analyze'],
                'training': {'gateway': True, 'supported_tasks': ['lora']},
            }),
        ))
        session.add(TrainingJob(
            id='train-visible-1',
            title='EC LoRA 训练',
            department='EC',
            created_by='admin',
            status='running',
            job_type='lora',
            target_gateway_id='node-analysis-1',
        ))
        session.add(TrainingJob(
            id='train-dispatch-failed-1',
            title='EC LoRA 分发失败残留',
            department='EC',
            created_by='admin',
            status='queued',
            failure_stage='dispatch',
            job_type='lora',
            target_gateway_id='node-analysis-1',
        ))
        await session.commit()

    resp = await client.get('/api/aiclaw/instances')
    assert resp.status_code == 200
    row = next(item for item in resp.json() if item['id'] == 'node-analysis-1')
    assert row['agent_purpose'] == 'analysis'
    assert row['analysis']['agent'] is True
    assert row['training']['active_jobs_count'] == 1
    assert row['active_training_jobs'][0]['id'] == 'train-visible-1'
    assert row['active_training_jobs'][0]['title'] == 'EC LoRA 训练'
    assert all(item['id'] != 'train-dispatch-failed-1' for item in row['active_training_jobs'])

    detail_resp = await client.get('/api/aiclaw/instances/node-analysis-1')
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail['agent_purpose'] == 'analysis'
    assert detail['analysis']['ops'] == ['intelligence.analyze']
    assert detail['active_training_jobs_count'] == 1


@pytest.mark.asyncio
async def test_chat_model_context_resolves_authorized_deployment_from_db(client):
    import app.database as db_mod
    from app.aiclaw.router import _chat_message_with_model_context, _resolve_chat_model_context
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(TrainingJob(
            id='train-chat-1',
            title='商品推荐 LoRA',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_skill_id='skill-recommend',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-1',
            job_id='train-chat-1',
            department='EC',
            model_family='item_ranker',
            artifact_id='artifact-main',
            artifact_ref_json={'id': 'artifact-main', 'sha256': 'a' * 64},
            target_skill_ids_json=['skill-recommend'],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        await session.commit()

        resolved = await _resolve_chat_model_context(
            session,
            _chat_user(),
            {
                'model_deployment_id': 'deploy-chat-1',
                'model_family': 'client-spoofed-name',
                'training_job_id': 'train-chat-1',
                'artifact_id': 'artifact-main',
            },
        )

    assert resolved == {
        'model_deployment_id': 'deploy-chat-1',
        'model_family': 'item_ranker',
        'training_job_id': 'train-chat-1',
        'artifact_id': 'artifact-main',
        'artifact_sha256': 'a' * 64,
        'deployment_status': 'active',
        'inference_ready': 'false',
        'inference_disabled_reason': '训练后模型 deploy-chat-1 缺少可推理的模型产物 URI',
    }
    message = _chat_message_with_model_context('这版模型效果怎么样？', resolved)
    assert message.startswith('<|im_start|>system')
    assert '模型上下文：' in message
    assert '<|im_start|>user\n这版模型效果怎么样？\n<|im_end|>\n<|im_start|>assistant' in message
    assert '禁止复述或解释这些上下文字段' in message
    assert '必须完整输出“输入、输出、适用场景”三段' in message
    assert '模型: item_ranker' in message
    assert '部署状态: active' in message
    assert '[用户问题]' not in message
    assert '[SkillForge 模型对话上下文]' not in message
    assert 'client-spoofed-name' not in message


@pytest.mark.asyncio
async def test_chat_model_context_rejects_cross_department_deployment(client):
    import app.database as db_mod
    from app.aiclaw.router import _resolve_chat_model_context
    from app.common.exceptions import AppError
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(TrainingJob(
            id='train-chat-2',
            title='EC 模型',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-2',
            job_id='train-chat-2',
            department='EC',
            model_family='ec_ranker',
            artifact_id='artifact-main',
            artifact_ref_json={'id': 'artifact-main', 'sha256': 'b' * 64},
            target_skill_ids_json=[],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        await session.commit()

        with pytest.raises(AppError) as exc:
            await _resolve_chat_model_context(
                session,
                _chat_user(user_id='u-hr', department='HR'),
                {'model_deployment_id': 'deploy-chat-2'},
            )

    assert exc.value.code == 'AUTH_DEPARTMENT_DENIED'
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_chat_model_context_rejects_unreleased_or_mismatched_deployment(client):
    import app.database as db_mod
    from app.aiclaw.router import _resolve_chat_model_context
    from app.common.exceptions import AppError
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(TrainingJob(
            id='train-chat-3',
            title='待审批模型',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-3',
            job_id='train-chat-3',
            department='EC',
            model_family='pending_ranker',
            artifact_id='artifact-main',
            artifact_ref_json={'id': 'artifact-main', 'sha256': 'c' * 64},
            target_skill_ids_json=[],
            status='awaiting_review',
            rollout_percent=0,
            requested_by='admin',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-4',
            job_id='train-chat-3',
            department='EC',
            model_family='active_ranker',
            artifact_id='artifact-main',
            artifact_ref_json={'id': 'artifact-main', 'sha256': 'd' * 64},
            target_skill_ids_json=[],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        await session.commit()

        with pytest.raises(AppError) as pending_exc:
            await _resolve_chat_model_context(
                session,
                _chat_user(),
                {'model_deployment_id': 'deploy-chat-3'},
            )
        with pytest.raises(AppError) as artifact_exc:
            await _resolve_chat_model_context(
                session,
                _chat_user(),
                {'model_deployment_id': 'deploy-chat-4', 'artifact_id': 'other-artifact'},
            )

    assert pending_exc.value.code == 'INVALID_STATUS'
    assert artifact_exc.value.code == 'PARAM_INVALID'


@pytest.mark.asyncio
async def test_chat_model_inference_routes_to_deployment_gateway(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.router import _chat_model_inference_events
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-chat-infer',
            name='模型推理节点',
            department='EC',
            gateway_url='ws://node-chat-infer',
            reload_hook_url='',
            reload_token='',
            is_active=True,
            agent_purpose='training',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({
                'ops': ['training.inference', 'training.artifact_status'],
                'gpu': [{'name': 'RTX 4060 Ti', 'vram_total_mb': 8188, 'vram_free_mb': 7652}],
            }),
        ))
        session.add(TrainingJob(
            id='train-chat-infer',
            title='商品推荐 LoRA',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_skill_id='skill-recommend',
            target_gateway_id='node-chat-infer',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-infer',
            job_id='train-chat-infer',
            department='EC',
            model_family='item_ranker',
            artifact_id='artifact-main',
            artifact_ref_json={'id': 'artifact-main', 'uri': 'file:///models/item_ranker', 'sha256': 'e' * 64},
            target_skill_ids_json=['skill-recommend'],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        session.add(TrainingJobTask(
            job_id='train-chat-infer',
            gateway_id='node-chat-infer',
            status='completed',
            progress=100,
            metrics_json={
                'gateway_result': {
                    'status': 'completed',
                    'metrics': {},
                    'artifacts': [
                        {'id': 'artifact-main', 'uri': 'file:///models/item_ranker', 'sha256': 'e' * 64},
                    ],
                },
            },
        ))
        await session.commit()

    ws = DummyWS()
    conn = await bridge_registry.register('node-chat-infer', ws)
    try:
        events = _chat_model_inference_events(
            _chat_user(),
            message='请测试这版模型',
            model_context={'model_deployment_id': 'deploy-chat-infer'},
        )
        started_task = asyncio.create_task(anext(events))
        await _wait_sent(ws)
        status_request = ws.sent[0]
        assert status_request['type'] == 'bridge_op'
        assert status_request['op'] == 'training.artifact_status'
        await conn.handle_bridge_op_response({
            'request_id': status_request['request_id'],
            'epoch': status_request['epoch'],
            'ok': True,
            'result': {'exists': True, 'sha256': 'e' * 64, 'size_bytes': 123},
        })
        started = await started_task
        assert started['state'] == 'started'
        assert started['gatewayId'] == 'node-chat-infer'

        final_task = asyncio.create_task(anext(events))
        await _wait_sent(ws, 2)
        request = ws.sent[1]
        assert request['type'] == 'bridge_op'
        assert request['op'] == 'training.inference'
        assert request['payload']['deployment_id'] == 'deploy-chat-infer'
        assert request['payload']['artifact_uri'] == 'file:///models/item_ranker'
        assert request['payload']['artifact_sha256'] == 'e' * 64
        assert request['payload']['max_new_tokens'] == 1024
        assert request['payload']['generation_policy']['mode'] == 'best_effort_by_model_and_gateway'
        assert '请测试这版模型' in request['payload']['prompt']

        await conn.handle_bridge_op_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'result': {
                'text': '<think>内部推理</think>\n\n训练后模型输出\nassistant\n重复输出',
                'metrics': {'generated_tokens': 6, 'duration_ms': 1500},
            },
        })
        final = await final_task
        assert final['state'] == 'final'
        assert final['text'] == '训练后模型输出'
        assert final['metrics']['generated_tokens'] == 6
        assert final['metrics']['tokens_per_second'] == 4
        assert final['metrics']['inference_runtime'] == 'local_bridge'
        assert final['metrics']['gateway_id'] == 'node-chat-infer'
        assert final['metrics']['effective_max_new_tokens'] == 1024
    finally:
        await bridge_registry.unregister('node-chat-infer')


@pytest.mark.asyncio
async def test_validate_chat_model_context_rejects_deployment_without_collected_artifact(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-chat-no-artifact',
            name='模型推理节点',
            department='EC',
            gateway_url='ws://node-chat-no-artifact',
            reload_hook_url='',
            reload_token='',
            is_active=True,
            agent_purpose='training',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({'ops': ['training.inference', 'training.artifact_status']}),
        ))
        session.add(TrainingJob(
            id='train-chat-no-artifact',
            title='占位 LoRA',
            department='EC',
            created_by='trainer',
            status='completed',
            job_type='lora',
            target_gateway_id='node-chat-no-artifact',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-no-artifact',
            job_id='train-chat-no-artifact',
            department='EC',
            model_family='lora',
            artifact_id='artifact-no-proof',
            artifact_ref_json={
                'id': 'artifact-no-proof',
                'uri': '/tmp/skillforge-state/training/artifacts/runtime-model.tar.gz',
                'sha256': 'c' * 64,
            },
            target_skill_ids_json=['skill-runtime-model-test'],
            status='active',
            rollout_percent=100,
            requested_by='trainer',
        ))
        await session.commit()

    ws = DummyWS()
    await bridge_registry.register('node-chat-no-artifact', ws)
    try:
        resp = await client.post(
            '/api/aiclaw/chat/model-context/validate',
            json={'model_context': {'model_deployment_id': 'deploy-chat-no-artifact'}},
        )
    finally:
        await bridge_registry.unregister('node-chat-no-artifact')

    assert resp.status_code == 200
    body = resp.json()
    assert body['ready'] is False
    assert '没有训练任务产物记录' in body['disabled_reason']
    assert body['context']['inference_ready'] == 'false'


@pytest.mark.asyncio
async def test_chat_model_inference_uses_large_gpu_budget(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.router import _chat_model_inference_events
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-chat-infer-24g',
            name='24G 模型推理节点',
            department='EC',
            gateway_url='ws://node-chat-infer-24g',
            reload_hook_url='',
            reload_token='',
            is_active=True,
            agent_purpose='training',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({
                'ops': ['training.inference', 'training.artifact_status'],
                'gpu': [{'name': 'RTX 4090', 'vram_total_mb': 24564, 'vram_free_mb': 24100}],
            }),
        ))
        session.add(TrainingJob(
            id='train-chat-infer-24g',
            title='商品推荐 LoRA',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_skill_id='skill-recommend',
            target_gateway_id='node-chat-infer-24g',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-infer-24g',
            job_id='train-chat-infer-24g',
            department='EC',
            model_family='skill-recommend:qwen3.5-4b-qlora',
            artifact_id='artifact-main-24g',
            artifact_ref_json={'id': 'artifact-main-24g', 'uri': 'file:///models/item_ranker', 'sha256': 'd' * 64},
            target_skill_ids_json=['skill-recommend'],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        session.add(TrainingJobTask(
            job_id='train-chat-infer-24g',
            gateway_id='node-chat-infer-24g',
            status='completed',
            progress=100,
            metrics_json={
                'gateway_result': {
                    'status': 'completed',
                    'metrics': {},
                    'artifacts': [
                        {'id': 'artifact-main-24g', 'uri': 'file:///models/item_ranker', 'sha256': 'd' * 64},
                    ],
                },
            },
        ))
        await session.commit()

    ws = DummyWS()
    conn = await bridge_registry.register('node-chat-infer-24g', ws)
    try:
        events = _chat_model_inference_events(
            _chat_user(),
            message='请测试这版模型',
            model_context={'model_deployment_id': 'deploy-chat-infer-24g'},
        )
        started_task = asyncio.create_task(anext(events))
        await _wait_sent(ws)
        status_request = ws.sent[0]
        assert status_request['op'] == 'training.artifact_status'
        await conn.handle_bridge_op_response({
            'request_id': status_request['request_id'],
            'epoch': status_request['epoch'],
            'ok': True,
            'result': {'exists': True, 'sha256': 'd' * 64, 'size_bytes': 123},
        })
        await started_task

        final_task = asyncio.create_task(anext(events))
        await _wait_sent(ws, 2)
        request = ws.sent[1]
        assert request['payload']['max_new_tokens'] == 4096

        await conn.handle_bridge_op_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'result': {'text': '24g model output', 'metrics': {'generated_tokens': 10, 'tokens_per_second': 12.5}},
        })
        final = await final_task
        assert final['metrics']['tokens_per_second'] == 12.5
        assert final['metrics']['effective_max_new_tokens'] == 4096
    finally:
        await bridge_registry.unregister('node-chat-infer-24g')


@pytest.mark.asyncio
async def test_chat_model_inference_reports_offline_deployment_gateway(client):
    import app.database as db_mod
    from app.aiclaw.router import _chat_model_inference_events
    from app.common.exceptions import AppError
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-chat-offline',
            name='离线推理节点',
            department='EC',
            gateway_url='ws://node-chat-offline',
            reload_hook_url='',
            reload_token='',
            is_active=True,
            agent_purpose='training',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({'ops': ['training.inference', 'training.artifact_status']}),
        ))
        session.add(TrainingJob(
            id='train-chat-offline',
            title='离线商品推荐 LoRA',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_gateway_id='node-chat-offline',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-offline',
            job_id='train-chat-offline',
            department='EC',
            model_family='item_ranker',
            artifact_id='artifact-main',
            artifact_ref_json={'id': 'artifact-main', 'uri': 'file:///models/item_ranker'},
            target_skill_ids_json=[],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        session.add(TrainingJobTask(
            job_id='train-chat-offline',
            gateway_id='node-chat-offline',
            status='completed',
            progress=100,
            metrics_json={
                'gateway_result': {
                    'status': 'completed',
                    'metrics': {},
                    'artifacts': [
                        {'id': 'artifact-main', 'uri': 'file:///models/item_ranker'},
                    ],
                },
            },
        ))
        await session.commit()

    with pytest.raises(AppError) as exc:
        await anext(_chat_model_inference_events(
            _chat_user(),
            message='请测试这版模型',
            model_context={'model_deployment_id': 'deploy-chat-offline'},
        ))

    assert exc.value.code == 'BRIDGE_OFFLINE'
    assert exc.value.detail['target_gateway_id'] == 'node-chat-offline'
    assert 'node-chat-offline' in exc.value.detail['detail']


@pytest.mark.asyncio
async def test_validate_chat_model_context_reports_inactive_inference_node(client):
    import app.database as db_mod
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-runtime-model-inactive',
            name='旧训练推理节点',
            department='AI小组',
            gateway_url='ws://node-runtime-model-inactive',
            reload_hook_url='',
            reload_token='',
            is_active=False,
            agent_purpose='skill_runtime',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({
                'ops': ['run_skill_script', 'training.inference', 'training.artifact_status'],
            }),
        ))
        session.add(TrainingJob(
            id='job-runtime-model-inactive',
            title='传统电商 LoRA',
            department='AI小组',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_gateway_id='node-runtime-model-inactive',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-runtime-model-inactive',
            job_id='job-runtime-model-inactive',
            department='AI小组',
            model_family='lora',
            artifact_id='artifact-runtime',
            artifact_ref_json={'id': 'artifact-runtime', 'uri': 'file:///models/runtime-model'},
            target_skill_ids_json=[],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        session.add(TrainingJobTask(
            job_id='job-runtime-model-inactive',
            gateway_id='node-runtime-model-inactive',
            status='completed',
            progress=100,
            metrics_json={
                'gateway_result': {
                    'status': 'completed',
                    'metrics': {},
                    'artifacts': [
                        {'id': 'artifact-runtime', 'uri': 'file:///models/runtime-model'},
                    ],
                },
            },
        ))
        await session.commit()

    resp = await client.post(
        '/api/aiclaw/chat/model-context/validate',
        json={'model_context': {'model_deployment_id': 'deploy-runtime-model-inactive'}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body['ready'] is False
    assert body['disabled_reason'] == '训练后模型推理节点 node-runtime-model-inactive 不存在或未启用'
    assert body['context']['model_deployment_id'] == 'deploy-runtime-model-inactive'
    assert body['context']['inference_ready'] == 'false'


@pytest.mark.asyncio
async def test_validate_chat_model_context_falls_back_to_online_inference_node(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-runtime-model-retired',
            name='旧训练推理节点',
            department='EC',
            gateway_url='ws://node-runtime-model-retired',
            reload_hook_url='',
            reload_token='',
            is_active=False,
            agent_purpose='skill_runtime',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({
                'ops': ['run_skill_script', 'training.inference', 'training.artifact_status'],
            }),
        ))
        session.add(OpenClawInstance(
            id='node-chat-infer-fallback',
            name='内容电商混合节点',
            department='销售二部',
            gateway_url='ws://node-chat-infer-fallback',
            reload_hook_url='',
            reload_token='',
            is_active=True,
            agent_purpose='mixed',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({
                'ops': ['run_skill_script', 'training.inference', 'training.artifact_status'],
            }),
        ))
        session.add(TrainingJob(
            id='job-runtime-model-fallback',
            title='传统电商 LoRA',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_gateway_id='node-runtime-model-retired',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-runtime-model-fallback',
            job_id='job-runtime-model-fallback',
            department='EC',
            model_family='lora',
            artifact_id='artifact-runtime-fallback',
            artifact_ref_json={
                'id': 'artifact-runtime-fallback',
                'uri': _bridge_state_artifact_uri('node-chat-infer-fallback'),
            },
            target_skill_ids_json=[],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        session.add(TrainingJobTask(
            job_id='job-runtime-model-fallback',
            gateway_id='node-runtime-model-retired',
            status='completed',
            progress=100,
            metrics_json={
                'gateway_result': {
                    'status': 'completed',
                    'metrics': {},
                    'artifacts': [
                        {
                            'id': 'artifact-runtime-fallback',
                            'uri': _bridge_state_artifact_uri('node-chat-infer-fallback'),
                        },
                    ],
                },
            },
        ))
        await session.commit()

    ws = DummyWS()
    conn = await bridge_registry.register('node-chat-infer-fallback', ws)
    try:
        resp_task = asyncio.create_task(client.post(
            '/api/aiclaw/chat/model-context/validate',
            json={'model_context': {
                'model_deployment_id': 'deploy-runtime-model-fallback',
                'target_gateway_id': 'node-runtime-model-retired',
            }},
        ))
        await _wait_sent(ws)
        request = ws.sent[0]
        assert request['op'] == 'training.artifact_status'
        await conn.handle_bridge_op_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'result': {'exists': True, 'size_bytes': 123},
        })
        resp = await resp_task
    finally:
        await bridge_registry.unregister('node-chat-infer-fallback')

    assert resp.status_code == 200
    body = resp.json()
    assert body['ready'] is True
    assert body['disabled_reason'] == ''
    assert body['context']['model_deployment_id'] == 'deploy-runtime-model-fallback'
    assert body['context']['target_gateway_id'] == 'node-chat-infer-fallback'
    assert body['context']['bound_gateway_id'] == 'node-runtime-model-retired'
    assert body['context']['inference_ready'] == 'true'
    assert '已切换到在线推理节点 node-chat-infer-fallback' in body['context']['inference_gateway_fallback_reason']


@pytest.mark.asyncio
async def test_chat_model_inference_routes_retired_gateway_to_online_fallback(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.router import _chat_model_inference_events
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-chat-retired',
            name='旧推理节点',
            department='EC',
            gateway_url='ws://node-chat-retired',
            reload_hook_url='',
            reload_token='',
            is_active=False,
            agent_purpose='skill_runtime',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({'ops': ['training.inference', 'training.artifact_status']}),
        ))
        session.add(OpenClawInstance(
            id='node-chat-fallback',
            name='在线混合推理节点',
            department='销售二部',
            gateway_url='ws://node-chat-fallback',
            reload_hook_url='',
            reload_token='',
            is_active=True,
            agent_purpose='mixed',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({'ops': ['run_skill_script', 'training.inference', 'training.artifact_status']}),
        ))
        session.add(TrainingJob(
            id='train-chat-fallback',
            title='传统电商 LoRA',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_gateway_id='node-chat-retired',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-fallback',
            job_id='train-chat-fallback',
            department='EC',
            model_family='lora',
            artifact_id='artifact-fallback',
            artifact_ref_json={
                'id': 'artifact-fallback',
                'uri': _bridge_state_artifact_uri('node-chat-fallback'),
                'sha256': 'f' * 64,
            },
            target_skill_ids_json=[],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        session.add(TrainingJobTask(
            job_id='train-chat-fallback',
            gateway_id='node-chat-retired',
            status='completed',
            progress=100,
            metrics_json={
                'gateway_result': {
                    'status': 'completed',
                    'metrics': {},
                    'artifacts': [
                        {
                            'id': 'artifact-fallback',
                            'uri': _bridge_state_artifact_uri('node-chat-fallback'),
                            'sha256': 'f' * 64,
                        },
                    ],
                },
            },
        ))
        await session.commit()

    ws = DummyWS()
    conn = await bridge_registry.register('node-chat-fallback', ws)
    try:
        events = _chat_model_inference_events(
            _chat_user(user_id='admin', role='admin', can_view_all=True),
            message='请测试这版模型',
            model_context={
                'model_deployment_id': 'deploy-chat-fallback',
                'target_gateway_id': 'node-chat-retired',
            },
        )
        started_task = asyncio.create_task(anext(events))
        await _wait_sent(ws)
        status_request = ws.sent[0]
        assert status_request['type'] == 'bridge_op'
        assert status_request['op'] == 'training.artifact_status'
        await conn.handle_bridge_op_response({
            'request_id': status_request['request_id'],
            'epoch': status_request['epoch'],
            'ok': True,
            'result': {'exists': True, 'sha256': 'f' * 64, 'size_bytes': 123},
        })
        started = await started_task
        assert started['state'] == 'started'
        assert started['gatewayId'] == 'node-chat-fallback'

        final_task = asyncio.create_task(anext(events))
        await _wait_sent(ws, 2)
        request = ws.sent[1]
        assert request['type'] == 'bridge_op'
        assert request['op'] == 'training.inference'
        assert request['payload']['deployment_id'] == 'deploy-chat-fallback'
        assert request['payload']['model_context']['target_gateway_id'] == 'node-chat-fallback'
        assert request['payload']['model_context']['bound_gateway_id'] == 'node-chat-retired'
        assert '原绑定节点: node-chat-retired' in request['payload']['prompt']
        assert '推理节点切换:' in request['payload']['prompt']

        await conn.handle_bridge_op_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'result': {'text': 'fallback model output'},
        })
        final = await final_task
        assert final['state'] == 'final'
        assert final['gatewayId'] == 'node-chat-fallback'
        assert final['text'] == 'fallback model output'
    finally:
        await bridge_registry.unregister('node-chat-fallback')


@pytest.mark.asyncio
async def test_validate_chat_model_context_reports_missing_artifact_on_online_fallback(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='node-artifact-retired',
            name='旧推理节点',
            department='EC',
            gateway_url='ws://node-artifact-retired',
            reload_hook_url='',
            reload_token='',
            is_active=False,
            agent_purpose='skill_runtime',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({'ops': ['training.inference', 'training.artifact_status']}),
        ))
        session.add(OpenClawInstance(
            id='node-artifact-fallback',
            name='在线推理节点',
            department='销售二部',
            gateway_url='ws://node-artifact-fallback',
            reload_hook_url='',
            reload_token='',
            is_active=True,
            agent_purpose='mixed',
            bridge_gateway_kind='openclaw',
            bridge_capabilities_json=json.dumps({'ops': ['training.inference', 'training.artifact_status']}),
        ))
        session.add(TrainingJob(
            id='train-chat-artifact-missing',
            title='传统电商占位 LoRA',
            department='EC',
            created_by='admin',
            status='completed',
            job_type='lora',
            target_gateway_id='node-artifact-retired',
        ))
        session.add(TrainingModelDeployment(
            id='deploy-chat-artifact-missing',
            job_id='train-chat-artifact-missing',
            department='EC',
            model_family='lora',
            artifact_id='artifact-missing',
            artifact_ref_json={
                'id': 'artifact-missing',
                'uri': _bridge_state_artifact_uri('node-artifact-fallback'),
                'sha256': 'c' * 64,
            },
            target_skill_ids_json=[],
            status='active',
            rollout_percent=100,
            requested_by='admin',
        ))
        session.add(TrainingJobTask(
            job_id='train-chat-artifact-missing',
            gateway_id='node-artifact-retired',
            status='completed',
            progress=100,
            metrics_json={
                'gateway_result': {
                    'status': 'completed',
                    'metrics': {},
                    'artifacts': [
                        {
                            'id': 'artifact-missing',
                            'uri': _bridge_state_artifact_uri('node-artifact-fallback'),
                            'sha256': 'c' * 64,
                        },
                    ],
                },
            },
        ))
        await session.commit()

    ws = DummyWS()
    conn = await bridge_registry.register('node-artifact-fallback', ws)
    try:
        resp_task = asyncio.create_task(client.post(
            '/api/aiclaw/chat/model-context/validate',
            json={'model_context': {
                'model_deployment_id': 'deploy-chat-artifact-missing',
                'target_gateway_id': 'node-artifact-retired',
            }},
        ))
        await _wait_sent(ws)
        request = ws.sent[0]
        assert request['op'] == 'training.artifact_status'
        await conn.handle_bridge_op_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'result': {'exists': False, 'reason': 'training artifact file not found'},
        })
        resp = await resp_task
    finally:
        await bridge_registry.unregister('node-artifact-fallback')

    assert resp.status_code == 200
    body = resp.json()
    assert body['ready'] is False
    assert body['context']['target_gateway_id'] == 'node-artifact-fallback'
    assert body['context']['bound_gateway_id'] == 'node-artifact-retired'
    assert '推理节点 node-artifact-fallback 上不存在' in body['disabled_reason']
    assert body['context']['inference_ready'] == 'false'


@pytest.mark.asyncio
async def test_chat_ws_target_rejects_cross_department_agent(client):
    import app.database as db_mod
    from app.aiclaw.router import _get_accessible_chat_instance
    from app.common.exceptions import AppError
    from app.execution.models import OpenClawInstance

    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='inst-chat-guard-1',
            name='EC Agent',
            department='EC',
            gateway_url='ws://127.0.0.1:18789',
            reload_hook_url='http://127.0.0.1:9000/reload',
            reload_token='',
            is_active=True,
        ))
        await session.commit()

    with pytest.raises(AppError) as exc:
        await _get_accessible_chat_instance('inst-chat-guard-1', _chat_user(user_id='u-hr', department='HR'))

    assert exc.value.code == 'AUTH_DEPARTMENT_DENIED'


@pytest.mark.asyncio
async def test_run_skill_script_bridge_timeout_allows_long_collection():
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient

    ws = DummyWS()
    conn = await bridge_registry.register('inst-long-run', ws)
    try:
        task = asyncio.create_task(
            AIClawClient('inst-long-run').run_skill_script(
                'tmall-link-decline-analysis-v2',
                payload={},
                timeout=7200,
            )
        )
        await asyncio.sleep(0)
        request = ws.sent[0]
        assert request['type'] == 'bridge_op'
        assert request['op'] == 'run_skill_script'
        assert request['payload']['timeout'] == 7200

        await conn.handle_bridge_op_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'result': {'ok': True},
        })
        assert await task == {'ok': True}
    finally:
        await bridge_registry.unregister('inst-long-run')


@pytest.mark.asyncio
async def test_get_agent_skills_includes_skill_git_version(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.execution.models import OpenClawInstance, SkillSyncAttempt
    from app.skills.core.models import Skill

    full_commit = '1234567890abcdef1234567890abcdef12345678'
    deployed_commit = 'abcdef1234567890abcdef1234567890abcdef12'
    async with db_mod.async_session_factory() as session:
        session.add(OpenClawInstance(
            id='inst-git-1',
            name='Git节点',
            department='AI小组',
            gateway_url='http://inst-git-1',
            reload_hook_url='',
            reload_token='',
            bridge_gateway_kind='aiclaw',
            is_active=True,
        ))
        session.add(Skill(
            id='skill-git-1',
            name='Git Skill',
            department='AI小组',
            status='active',
            current_version='v1.2.3',
            git_commit=full_commit,
        ))
        session.add(SkillSyncAttempt(
            job_id=1,
            skill_id='skill-git-1',
            version_tag='skill-git-1/v1.2.3',
            instance_id='inst-git-1',
            status='succeeded',
            result={'git_commit_full': deployed_commit, 'git_commit': deployed_commit[:8]},
        ))
        await session.commit()

    ws = DummyWS()
    conn = await bridge_registry.register('inst-git-1', ws)
    try:
        task = asyncio.create_task(client.get('/api/aiclaw/instances/inst-git-1/agents/main/skills'))
        for _ in range(20):
            await asyncio.sleep(0.01)
            if ws.sent:
                break
        assert ws.sent
        request = ws.sent[0]
        assert request['method'] == 'skills.status'

        await conn.handle_forward_response({
            'request_id': request['request_id'],
            'epoch': request['epoch'],
            'ok': True,
            'payload': {'skills': [{'id': 'skill-git-1', 'status': 'loaded'}]},
        })
        resp = await task
        assert resp.status_code == 200
        item = resp.json()['items'][0]
        assert item['git_commit'] == full_commit[:8]
        assert item['git_commit_full'] == full_commit
        assert item['deployed_git_commit'] == deployed_commit[:8]
        assert item['deployed_git_commit_full'] == deployed_commit
        assert item['docker_git_commit'] == deployed_commit[:8]
        assert item['sync_version_tag'] == 'skill-git-1/v1.2.3'
        assert item['current_version'] == 'v1.2.3'
    finally:
        await bridge_registry.unregister('inst-git-1')


@pytest.mark.asyncio
async def test_aiclaw_instance_create_and_download_script(client):
    create_resp = await client.post(
        '/api/aiclaw/instances',
        json={
            'id': 'inst-create-1',
            'name': '实例一',
            'department': 'AI',
            'gateway_url': 'ws://127.0.0.1:18789',
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created['enrollment_token']

    script_resp = await client.get(
        f"/api/aiclaw/instances/{created['id']}/bridge-script",
        params={'one_time_token': created['enrollment_token'], 'platform': 'linux'},
    )
    assert script_resp.status_code == 200
    assert 'ENROLLMENT_TOKEN' in script_resp.text
    assert created['enrollment_token'] in script_resp.text


@pytest.mark.asyncio
async def test_platform_default_media_instance_can_be_created_without_department(client):
    create_resp = await client.post(
        '/api/aiclaw/instances',
        json={
            'id': 'platform-media-5080-test',
            'name': 'RTX 5080 media node',
            'department': '',
            'gateway_url': 'ws://127.0.0.1:18789',
            'agent_purpose': 'media',
            'is_platform_default': True,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    created = create_resp.json()
    assert created['department'] in {None, ''}
    assert created['agent_purpose'] == 'media'
    assert created['is_platform_default'] is True
    assert created['enrollment_token']


@pytest.mark.asyncio
async def test_agent_department_coverage_reports_missing_and_platform_fallback(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.execution.models import OpenClawInstance

    async with db_mod.async_session_factory() as session:
        session.add_all([
            OpenClawInstance(
                id='ec-runtime',
                name='EC 执行 Agent',
                department='EC',
                gateway_url='ws://ec-runtime',
                reload_hook_url='',
                reload_token='',
                is_active=True,
                agent_purpose='skill_runtime',
                bridge_gateway_kind='openclaw',
                bridge_capabilities_json=json.dumps({'ops': ['run_agent_skill']}),
            ),
            OpenClawInstance(
                id='platform-analysis',
                name='平台分析 Agent',
                department='AI',
                gateway_url='ws://platform-analysis',
                reload_hook_url='',
                reload_token='',
                is_active=True,
                is_platform_default=True,
                agent_purpose='analysis',
                bridge_gateway_kind='openclaw',
                bridge_capabilities_json=json.dumps({'ops': ['intelligence.analyze']}),
            ),
            OpenClawInstance(
                id='platform-training',
                name='平台训练 Agent',
                department='AI',
                gateway_url='ws://platform-training',
                reload_hook_url='',
                reload_token='',
                is_active=True,
                is_platform_default=True,
                agent_purpose='training',
                bridge_gateway_kind='openclaw',
                bridge_capabilities_json=json.dumps({
                    'ops': ['training.submit_job', 'training.collect_result', 'training.inference'],
                    'training': {'gateway': True, 'supported_tasks': ['lora'], 'gpu_count': 1},
                }),
            ),
        ])
        await session.commit()

    class DummyOnline:
        def __init__(self, instance_id: str):
            self.instance_id = instance_id

        async def close(self, code: int = 1000, reason: str = ''):
            return None

    for instance_id in ['ec-runtime', 'platform-analysis', 'platform-training']:
        await bridge_registry.register(instance_id, DummyOnline(instance_id))
    try:
        resp = await client.get('/api/aiclaw/departments/agent-coverage')
        assert resp.status_code == 200, resp.text
        rows = {item['department']: item for item in resp.json()['items']}
        assert rows['EC']['status'] == 'fallback'
        assert rows['EC']['capabilities']['skill_runtime']['ready'] is True
        assert rows['EC']['capabilities']['analysis']['fallback_ready'] is True
        assert rows['EC']['capabilities']['training']['fallback_ready'] is True
        assert rows['EC']['fallback'] == ['analysis', 'training']
        assert rows['EC']['capabilities']['skill_runtime']['flow'] == {
            'input_channels': ['skill_run_request', 'node_schedule_snapshot'],
            'control_ops': ['run_agent_skill', 'run_skill_script'],
            'output_channels': ['execution_run', 'decision_log', 'execution_artifact'],
            'fallback': False,
        }
        assert rows['EC']['capabilities']['analysis']['flow']['fallback'] is True
        assert rows['EC']['controlled_node_count'] == 1
        runtime_node = rows['EC']['controlled_nodes'][0]
        assert runtime_node['id'] == 'ec-runtime'
        assert runtime_node['capabilities'] == ['skill_runtime']
        assert runtime_node['input_contract_clear'] is True
        assert runtime_node['agent_contract']['complete'] is True
        assert runtime_node['agent_contract']['missing'] == []
        assert runtime_node['agent_contract']['input_channels'] == [
            'node_schedule_snapshot',
            'skill_run_request',
        ]
        assert runtime_node['agent_contract']['control_ops'] == [
            'run_agent_skill',
            'run_skill_script',
        ]
        assert runtime_node['agent_contract']['required_ops_any'] == {
            'skill_runtime': ['run_agent_skill', 'run_skill_script'],
        }
        assert runtime_node['agent_contract']['available_control_ops'] == ['run_agent_skill']
        assert runtime_node['agent_contract']['output_channels'] == [
            'decision_log',
            'execution_artifact',
            'execution_run',
        ]
        assert runtime_node['agent_contract']['missing_ops'] == []
        assert runtime_node['agent_contract']['submission_ready'] is True
        assert runtime_node['agent_contract']['lifecycle_ready'] is True
        assert runtime_node['agent_contract']['flow_traceable'] is True
        assert runtime_node['flow']['skill_runtime']['input_channels'] == [
            'skill_run_request',
            'node_schedule_snapshot',
        ]
        assert 'decision_log' in runtime_node['lineage_channels']
    finally:
        for instance_id in ['ec-runtime', 'platform-analysis', 'platform-training']:
            await bridge_registry.unregister(instance_id)


@pytest.mark.asyncio
async def test_agent_department_coverage_reports_training_lifecycle_op_gaps(client):
    import app.database as db_mod
    from app.aiclaw.bridge_registry import bridge_registry
    from app.execution.models import OpenClawInstance

    async with db_mod.async_session_factory() as session:
        session.add(
            OpenClawInstance(
                id='ec-training-submit-only',
                name='EC 训练提交 Agent',
                department='EC',
                gateway_url='ws://ec-training-submit-only',
                reload_hook_url='',
                reload_token='',
                is_active=True,
                agent_purpose='training',
                bridge_gateway_kind='openclaw',
                bridge_capabilities_json=json.dumps({
                    'ops': ['training.submit_job'],
                    'training': {'gateway': True, 'supported_tasks': ['lora'], 'gpu_count': 1},
                }),
            )
        )
        await session.commit()

    class DummyOnline:
        def __init__(self, instance_id: str):
            self.instance_id = instance_id

        async def close(self, code: int = 1000, reason: str = ''):
            return None

    await bridge_registry.register('ec-training-submit-only', DummyOnline('ec-training-submit-only'))
    try:
        resp = await client.get('/api/aiclaw/departments/agent-coverage')
        assert resp.status_code == 200, resp.text
        rows = {item['department']: item for item in resp.json()['items']}
        training = rows['EC']['capabilities']['training']
        assert training['count'] == 1
        assert training['online'] == 1
        assert training['ready'] is False
        controlled_nodes = {item['id']: item for item in rows['EC']['controlled_nodes']}
        assert 'ec-training-submit-only' in controlled_nodes
        contract = controlled_nodes['ec-training-submit-only']['agent_contract']
        assert contract['complete'] is False
        assert contract['submission_ready'] is True
        assert contract['lifecycle_ready'] is False
        assert contract['missing'] == ['control_ops']
        assert contract['missing_ops'] == ['training.collect_result', 'training.inference']
        assert contract['required_ops'] == [
            'training.collect_result',
            'training.inference',
            'training.submit_job',
        ]
    finally:
        await bridge_registry.unregister('ec-training-submit-only')


@pytest.mark.asyncio
async def test_regenerate_enrollment_invalidates_old_download_token(client):
    create_resp = await client.post(
        '/api/aiclaw/instances',
        json={
            'id': 'inst-create-2',
            'name': '实例二',
            'department': 'AI',
            'gateway_url': 'ws://127.0.0.1:18789',
        },
    )
    first = create_resp.json()
    regen_resp = await client.post(f"/api/aiclaw/instances/{first['id']}/regenerate-enrollment")
    assert regen_resp.status_code == 200
    second = regen_resp.json()
    assert second['enrollment_token'] != first['enrollment_token']

    old_download = await client.get(
        f"/api/aiclaw/instances/{first['id']}/bridge-script",
        params={'one_time_token': first['enrollment_token'], 'platform': 'linux'},
    )
    assert old_download.status_code == 400

    new_download = await client.get(
        f"/api/aiclaw/instances/{first['id']}/bridge-script",
        params={'one_time_token': second['enrollment_token'], 'platform': 'linux'},
    )
    assert new_download.status_code == 200


@pytest.mark.asyncio
async def test_reset_binding_and_rotate_key_endpoints(client):
    create_resp = await client.post(
        '/api/aiclaw/instances',
        json={
            'id': 'inst-create-3',
            'name': '实例三',
            'department': 'AI',
            'gateway_url': 'ws://127.0.0.1:18789',
        },
    )
    created = create_resp.json()

    reset_resp = await client.post(f"/api/aiclaw/instances/{created['id']}/reset-binding")
    assert reset_resp.status_code == 200
    assert reset_resp.json()['binding_reset'] is True

    rotate_resp = await client.post(f"/api/aiclaw/instances/{created['id']}/rotate-key")
    assert rotate_resp.status_code == 200
    body = rotate_resp.json()
    assert body['pending_rotation'] is True
    assert body['rotation_token']


def test_bridge_script_render_outputs_valid_python():
    from app.aiclaw.script_generator import BRIDGE_SOURCE_PATH, BRIDGE_VERSION, render

    script = render(
        instance_id="inst-render-1",
        enrollment_token="tok-render-1",
        skillforge_ws_url="ws://localhost:8000/api/aiclaw/bridge/ws",
        platform="linux",
        local_aiclaw_url="ws://127.0.0.1:18789",
    )

    assert 'json.dumps({' in script
    bridge_source = BRIDGE_SOURCE_PATH.read_text(encoding="utf-8")
    assert f'BRIDGE_VERSION = "{BRIDGE_VERSION}"' in bridge_source
    assert "ping_interval=20," in bridge_source
    assert "ping_timeout=20," in bridge_source
    assert f"version={BRIDGE_VERSION}" in script
    assert '"fingerprint": _bridge_fingerprint()' in script
    assert "def _install_linux_systemd" in script
    assert "def _ensure_linux_autostart_from_daemon" in script
    assert "crontab @reboot fallback" in script
    assert 'ExecStart={python_quoted} {script_quoted} --daemon' in script
    assert 'os.environ.get("SKILLFORGE_BRIDGE_DAEMON") == "1"' in script
    # bridge 状态目录下的关键文件名：env.json / device.key / bridge.py
    assert 'env.json' in script
    assert 'device.key' in script
    compile(script, "<bridge_script>", "exec")
